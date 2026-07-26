"""
p2workflowy V2: Gemini API クライアント
google-genai SDK を使用したリトライ付き LLM 呼び出しラッパー。
"""

import json
import os
import time
import asyncio
import enum
import re
import csv
import random
from typing import Any, Callable, List, Tuple, Dict, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import threading
from pathlib import Path

from google import genai
from google.genai import types
from aiolimiter import AsyncLimiter
from .config import GEMINI_API_KEY, STATE_DIR, load_coreprompts, print_log


# プロンプト定数の読み込み
_PROMPTS: dict | None = None

def _get_prompts() -> dict:
    global _PROMPTS
    if _PROMPTS is None:
        _PROMPTS = load_coreprompts()
    return _PROMPTS


def get_default_model(purpose: str = "default") -> str:
    """coreprompts.json から用途に応じた DEFAULT_MODEL を取得。
    
    Args:
        purpose: "default" (CLI/高品質), "free" (無料ティア/Web), "vlm" (OCR/画像認識)
    """
    prompts = _get_prompts()

    if purpose == "resume":
        override = os.environ.get("DEFAULT_MODEL_RESUME") or prompts.get("DEFAULT_MODEL_RESUME", "")
        if override:
            return override
        purpose = "default"  # 空 → 通常の tier 追従にフォールバック

    # ティア状態のチェック: FREE であれば "default" リクエストも "free" 用に振り分ける
    effective_purpose = purpose
    if purpose == "default" and tier_manager.current_tier == GeminiTier.FREE:
        effective_purpose = "free"

    if effective_purpose == "free":
        return prompts.get("DEFAULT_MODEL_FREE", prompts.get("DEFAULT_MODEL", "gemini-3.1-flash-lite"))
    elif effective_purpose == "vlm":
        return prompts.get("DEFAULT_MODEL_VLM", "gemini-3.1-flash-lite")
    return prompts.get("DEFAULT_MODEL", "gemini-3-flash-preview")


class GeminiTier(enum.Enum):
    PAID = "paid"
    FREE = "free"
    UNKNOWN = "unknown"

class TierManager:
    """APIキーのティア（有料/無料）状態を管理するシングルトン。

    Webアプリの並行パイプライン実行はそれぞれ別スレッド（server.py の asyncio.to_thread）で
    動くため、内部状態は threading.local() を裏に持つプロパティとしてスレッドごとに分離する。
    `tier_manager` オブジェクト自体は今まで通りプロセス全体で単一のシングルトンとして import
    され続け、外部からの属性アクセス（`tier_manager.current_tier` 等の読み書き）も無変更。
    """
    _instance = None
    _local = threading.local()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_local(self):
        if not hasattr(self._local, "current_tier"):
            self._local.current_tier = GeminiTier.UNKNOWN
            self._local.was_downgraded = False

    @property
    def current_tier(self) -> GeminiTier:
        self._ensure_local()
        return self._local.current_tier

    @current_tier.setter
    def current_tier(self, value: GeminiTier):
        self._ensure_local()
        self._local.current_tier = value

    @property
    def was_downgraded(self) -> bool:
        self._ensure_local()
        return self._local.was_downgraded

    @was_downgraded.setter
    def was_downgraded(self, value: bool):
        self._ensure_local()
        self._local.was_downgraded = value

    def set_tier(self, tier: GeminiTier):
        if self.current_tier != tier:
            print_log(f"  [TierManager] Tier set to: {tier.value}")
            self.current_tier = tier
        # ティアを再設定した時点で、過去のダウンシフト状態はクリアする
        self.was_downgraded = False

    def downgrade(self):
        # ログは初回のみ出力するが、ティアの設定自体は常に行う
        if not self.was_downgraded:
            print_log("  [TierManager] !!! Resource Limit/High Demand (503/429) detected. Downgrading to FREE tier mode (Lite). !!!")
            self.was_downgraded = True
        self.current_tier = GeminiTier.FREE

tier_manager = TierManager()


class KeyRotator:
    """CLI (main.py) 用の複数APIキーローテーション管理シングルトン。

    既定ではプロセスグローバルな状態（スレッドローカル化しない）— CLIはスレッドを使わず単一
    プロセス内で完結するため（Phase4の並行も単一イベントループ内の asyncio.Semaphore のみ）。
    forward-only（一度進んだキーインデックスは戻らない、TierManager.downgrade() と同じ設計
    思想）。main.py が起動時に configure() を一度だけ呼ぶ。server.py は一切呼ばないため、
    Webアプリの挙動には影響しない（is_configured() が常に False のまま）。

    2つの使われ方がある:
      1. リアクティブなフォールバック: 429/503 検知時に advance() で次のキーへ前進する
         （forward-only。call_gemini/call_gemini_async のリトライループが呼ぶ）。
      2. プロアクティブな負荷分散: pool_keys() が返す無料キー群へ、呼び出し元（Phase4 の
         ParallelTranslator / Phase1 の OCRManager）がバッチ・ページ単位でラウンドロビン
         割り当てする（docs/model_optimization.md §8）。

    さらに restrict_to() で「呼び出したスレッドだけが使えるキーの部分集合」を設定できる
    （書籍モードの章並列化で、章スレッドごとにキーを1本ずつ排他割り当てするためのフック）。
    制限を設定していないスレッドの挙動はプロセスグローバル状態そのままで一切変わらない。
    """
    _instance = None
    _local = threading.local()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._keys = []
            cls._instance._tiers = []
            cls._instance._index = 0
        return cls._instance

    # --- スレッドローカルな使用キー制限 ---

    def restrict_to(self, keys: List[Optional[str]], tiers: Optional[List[str]] = None) -> None:
        """呼び出したスレッドに限り、使用キーを keys の部分集合に制限する。

        制限中は current()/current_tier()/pool_keys()/has_next()/advance()/index/count が
        すべてこの部分集合に対して動作し、プロセスグローバルな _keys/_index には一切触れない
        （＝他スレッドから完全に不可視）。書籍モードの章並列化で、章スレッドごとに別キーを
        排他割り当てし、同じ (キー, モデル) レーンを複数スレッドが多重に叩かないようにする
        ための下ごしらえ（レートリミッタは threading.local() ベースなので、スレッド間で
        レーンを共有すると枠を二重に消費して429が多発する）。
        """
        pairs = [(k, t) for k, t in zip(keys, tiers or [None] * len(keys)) if k]
        self._local.keys = [k for k, _ in pairs]
        self._local.tiers = [t for _, t in pairs]
        self._local.index = 0

    def clear_restriction(self) -> None:
        """このスレッドの使用キー制限を解除し、プロセスグローバルな状態に戻す。"""
        self._local.keys = None
        self._local.tiers = None
        self._local.index = 0

    def is_restricted(self) -> bool:
        return bool(getattr(self._local, "keys", None))

    def _view(self) -> tuple:
        """(keys, tiers) の現在有効なビューを返す（制限中はスレッドローカル、それ以外はグローバル）。"""
        if self.is_restricted():
            return self._local.keys, self._local.tiers
        return self._keys, self._tiers

    def _get_index(self) -> int:
        if self.is_restricted():
            return getattr(self._local, "index", 0)
        return self._index

    def _set_index(self, value: int) -> None:
        if self.is_restricted():
            self._local.index = value
        else:
            self._index = value

    # --- 通常 API ---

    def configure(self, keys: List[Optional[str]], tiers: Optional[List[str]] = None) -> None:
        """keys と同じ並びの tiers（例: ["free","free","paid"]）を渡すと、ローテーション後に
        現在のキーが有料かどうかを current_tier() で判定できる。省略時は全キー種別不明扱い。"""
        pairs = [(k, t) for k, t in zip(keys, tiers or [None] * len(keys)) if k]
        self._keys = [k for k, _ in pairs]
        self._tiers = [t for _, t in pairs]
        self._index = 0

    def is_configured(self) -> bool:
        return bool(self._view()[0])

    def current(self) -> Optional[str]:
        keys, _ = self._view()
        return keys[self._get_index()] if keys else None

    def current_tier(self) -> Optional[str]:
        """現在選択中のキーの種別（"free"/"paid"）。configure() に tiers を渡していなければ None。"""
        _, tiers = self._view()
        return tiers[self._get_index()] if tiers else None

    def pool_keys(self) -> List[str]:
        """tier が "free" のキーだけを並び順どおりに返す（キー軸ラウンドロビン割り当て用）。

        有料キーを含めないのは、無料枠専用ペース（1 req/4s/レーン）のリミッタを有料キーにも
        適用すると有料ユーザーを不必要に遅くしてしまうため（§7 で PAID tier をラウンドロビン
        対象外にしたのと同じ理由）。tiers 未設定（configure に tiers を渡していない）の場合は
        キー種別が判定できないため空リストを返し、キー軸のRRは自然に無効化される。
        """
        keys, tiers = self._view()
        if not keys or not tiers:
            return []
        return [k for k, t in zip(keys, tiers) if t == "free"]

    def has_next(self) -> bool:
        keys, _ = self._view()
        return bool(keys) and self._get_index() < len(keys) - 1

    def advance(self) -> Optional[str]:
        if self.has_next():
            self._set_index(self._get_index() + 1)
        return self.current()

    def best_available(self, is_available: Callable[[Optional[str]], bool]) -> Optional[str]:
        """429起点のフォールバック用。forward-only な advance() と異なり、クールダウンが
        明けて回復したキー（例: free1 が429でクールダウン中に free2 へ進んだ後、free1 が
        先に回復する）へ**戻れる**（docs/model_optimization.md §9、§6 既知の限界の解消）。

        優先順位は configure() に渡した並び順そのまま（CLI/Web とも free キーを先・paid
        キーを最後に渡す運用のため、"無料キーが1本でも生きているなら有料キーには落ちない"
        という既存の優先順位がそのまま保たれる）。現在のキーがまだ available ならそれを
        優先して返す（無駄な切替をしない）。現在のキー以外に available なキーがあれば、
        並び順で最初に見つかったものへ切り替える。**全キーが使用不可（＝全レーンが
        クールダウン中）の場合は、既存の forward-only な advance() にフォールバックする**
        （新しい情報が無い以上、従来どおりの挙動に委ねるのが安全なため。呼び出し元は
        戻り値が変化したかどうかで「実際に切り替わったか」を判定できる）。

        既存の advance() 自体はシグネチャ・forward-only の挙動とも一切変更していない
        （既存テストが依存しているため）。こちらは新規に追加した代替 API で、
        call_gemini/call_gemini_async の429/503リトライループが advance() の代わりに使う。
        """
        keys, _ = self._view()
        if not keys:
            return None
        current_key = self.current()
        if is_available(current_key):
            return current_key
        for k in keys:
            if k != current_key and is_available(k):
                self._set_index(keys.index(k))
                return self.current()
        # 全キー使用不可 → 新しい判断材料が無いので従来の forward-only にフォールバック
        return self.advance()

    @property
    def index(self) -> int:
        return self._get_index()

    @property
    def count(self) -> int:
        return len(self._view()[0])

key_rotator = KeyRotator()


class ModelRotator:
    """無料枠Liteモデルプール内でのフォワードオンリー・ローテーション（TierManagerと同じくスレッドローカル）。

    KeyRotatorがAPIキー単位のローテーションであるのに対し、こちらは同一キー・同一プロジェクト内で
    複数のLiteモデル（gemini-3.1-flash-lite / gemini-3.5-flash-lite 等）のRPD/RPMが別カウンターで
    管理されている性質を利用し、片方が429/503でリソース枯渇したときにもう片方へ切り替える。
    Web側の並行パイプライン実行にも対応できるよう TierManager と同じくスレッドローカルにする。
    """
    _local = threading.local()

    def _ensure_local(self):
        if not hasattr(self._local, "pool"):
            prompts = _get_prompts()
            pool = prompts.get("DEFAULT_MODEL_FREE_POOL") or [prompts.get("DEFAULT_MODEL_FREE", "gemini-3.1-flash-lite")]
            self._local.pool = pool
            self._local.index = 0

    def reset(self):
        self._ensure_local()
        self._local.index = 0

    def is_pool_member(self, model: str | None) -> bool:
        self._ensure_local()
        return model in self._local.pool

    def current(self) -> str:
        self._ensure_local()
        return self._local.pool[self._local.index]

    def has_next(self) -> bool:
        self._ensure_local()
        return self._local.index < len(self._local.pool) - 1

    def advance(self) -> str:
        self._ensure_local()
        if self.has_next():
            self._local.index += 1
        return self.current()

    def resolve(self, model: str | None) -> str | None:
        """model がプールのメンバーなら現在のローテーション先へ差し替える。プール外はそのまま返す。"""
        self._ensure_local()
        if model in self._local.pool:
            return self.current()
        return model

    def pool_models(self) -> List[str]:
        """プール全体のモデル名リストを返す（Phase4のラウンドロビン割り当て用）。"""
        self._ensure_local()
        return list(self._local.pool)

model_rotator = ModelRotator()


def get_free_pool_rate_limiters(api_key: str | None = None) -> Dict[str, AsyncLimiter]:
    """無料枠Liteプールの各モデルに独立した AsyncLimiter を割り当てて返す（Phase4ラウンドロビン用）。

    apply_tier_settings() の FREE tier 既定と同じレート（1 req / 4s ≒ 15RPM相当）をモデルごとに
    独立して持たせることで、単一リミッタ共有時よりも高いスループットを狙う。既存の
    apply_tier_settings() の (tier, api_key) キーとは別に (tier, api_key, model) でキーイングする
    ため、ローテーション（resolve()/advance()）用の共有リミッタとは衝突しない。
    """
    limiters = _get_limiters_dict()
    result = {}
    for m in model_rotator.pool_models():
        cache_key = (GeminiTier.FREE, api_key, m)
        if cache_key not in limiters:
            limiters[cache_key] = AsyncLimiter(1, 4.0)
        result[m] = limiters[cache_key]
    return result


class LaneCooldownRegistry:
    """(api_key, model) 単位のレーンクールダウンレジストリ（docs/model_optimization.md §9）。

    KeyRotator/ModelRotator/TierManager と異なり**意図的にスレッドローカルにしない**:
    「このキーのこのモデルは枯渇している」という事実はスレッドを跨いで共有されるのが正しい
    （プロセスグローバルな dict + threading.Lock）。(api_key, model) でキーイングするため、
    Web の別ユーザー（＝別キー）を誤って巻き込むこともなく、書籍の章並列化（章スレッドごと
    に別キーを排他割り当て、docs/model_optimization.md §8 の KeyRotator.restrict_to()）とも
    自然に整合する。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cooldowns: Dict[Tuple[Optional[str], Optional[str]], float] = {}

    def mark(self, api_key: Optional[str], model: Optional[str], seconds: float) -> None:
        """(api_key, model) レーンを seconds 秒クールダウンさせる。既に設定済みで、かつ
        まだそちらの方が長く残っている場合は短縮しない（同一レーンへの多重リクエストが
        並行して 429 を踏んでも、最も長いクールダウンが優先される）。"""
        until = time.time() + max(seconds, 0.0)
        key = (api_key, model)
        with self._lock:
            existing = self._cooldowns.get(key, 0.0)
            self._cooldowns[key] = max(existing, until)

    def is_cooling(self, api_key: Optional[str], model: Optional[str]) -> bool:
        with self._lock:
            until = self._cooldowns.get((api_key, model))
        return bool(until) and time.time() < until

    def remaining(self, api_key: Optional[str], model: Optional[str]) -> float:
        with self._lock:
            until = self._cooldowns.get((api_key, model), 0.0)
        return max(0.0, until - time.time())

    def clear(self) -> None:
        """全レーンのクールダウンを解除する（主にテスト用。reset_pipeline_state() からは
        意図的に呼ばない — 枯渇の事実はパイプライン・章を跨いで持続するのが正しいため、
        詳細は docs/model_optimization.md §9 参照）。"""
        with self._lock:
            self._cooldowns.clear()


lane_cooldown = LaneCooldownRegistry()


# --- 429/503 のクールダウン秒数決定（docs/model_optimization.md §9） ---
#
# RetryInfo.retryDelay は取れれば使うが、信頼性に難があるという報告があるため
# （429 errors despite waiting after retryDelay 等）、必ず上下限でクランプして使う。
# 加えて quotaMetric/quotaId から RPM/TPM/RPD のどれに起因するかを判別し、
# 回復スケールの大きく異なる RPD だけは別ロジック（日次リセット時刻までの残り秒数）にする。

COOLDOWN_MIN_SECONDS = 1.0
# RPM/TPM は1分単位のスライディングウィンドウでリセットされる（gemini_models.md §4）。
# retryDelayが取れない場合の既定値、および取れた場合のクランプ上限としても使う
# （60秒の2倍＝安全マージンを見て120秒を上限とする）。
COOLDOWN_RPM_TPM_SECONDS = 60.0
RETRY_DELAY_CLAMP_MAX_SECONDS = 120.0
# quotaMetric が判別できない場合（503含む）の安全側デフォルト。RPM/TPMより短くしているのは、
# 429/503全般では「本当にリソース枯渇かどうか」自体が不確かなケース（一時的なサーバ混雑等）
# を含むため、長時間ふさぐより早めに再挑戦させる方が実害が小さいという判断。
COOLDOWN_UNKNOWN_SECONDS = 30.0
# RPD はその日いっぱい回復しない（太平洋時間の深夜にリセット）。retryDelayは短すぎることが
# 多く信用できないため、実際の日次リセット時刻までの残り秒数を計算して使う。計算誤り・
# タイムゾーンデータ異常時の安全弁として下限1時間・上限24時間でクランプする。
COOLDOWN_RPD_MIN_SECONDS = 3600.0
COOLDOWN_RPD_MAX_SECONDS = 24 * 3600.0


def _seconds_until_next_pacific_midnight(now: Optional[datetime] = None) -> float:
    """次の太平洋時間深夜（Gemini APIのRPDリセット時刻）までの残り秒数。

    `now` を渡すとテストで任意時刻を注入できる（`time.time()`同様、本番では省略）。
    """
    tz = ZoneInfo("America/Los_Angeles")
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    next_day = (current + timedelta(days=1)).date()
    next_midnight = datetime(next_day.year, next_day.month, next_day.day, tzinfo=tz)
    return (next_midnight - current).total_seconds()


def _quota_violation_text(exc: BaseException) -> str:
    """429/503 エラーの詳細情報を検索用に小文字化した1本の文字列にまとめる。

    google-genai の APIError.details は元の JSON エラーボディ（'error'キー配下に
    'details': [...] としてRetryInfo/QuotaFailureが入る）をそのまま保持しているが、
    最終的にはメッセージ文字列化（str(exc)、APIError.__init__ が
    f'{code} {status}. {details}' の形で details を埋め込む）にも同じ情報が現れるため、
    構造化アクセスに失敗しても文字列マッチにフォールバックできる。
    `_classify_quota_violation()` / `_is_model_scoped_quota()` の共通ヘルパー。
    """
    text = ""
    details = getattr(exc, "details", None)
    if details is not None:
        try:
            text = json.dumps(details, ensure_ascii=False, default=str)
        except Exception:
            text = str(details)
    return f"{text} {exc}".lower()


def _classify_quota_violation(exc: BaseException) -> str:
    """429エラーの quotaMetric/quotaId から起因を判別する。'rpm'/'tpm'/'rpd'/'unknown' を返す。"""
    text = _quota_violation_text(exc)

    # トークン系（TPM）の quotaId/quotaMetric は "...token..." を含む（例:
    # GenerateContentInputTokensPerModelPerMinute-FreeTier）。RPDのトークン系quotaは
    # 現状ドキュメント上確認できないため、token系はTPM扱いを既定とする。
    if "token" in text:
        return "tpm"
    # 日次（RPD）の quotaId/quotaMetric は "day" を含む（例: PerDay, per_day）。
    if "day" in text:
        return "rpd"
    # 分次（RPM）の quotaId/quotaMetric は "minute" もしくは "requests" を含む。
    if "minute" in text or "requests" in text or "rpm" in text:
        return "rpm"
    return "unknown"


def _is_model_scoped_quota(exc: BaseException) -> bool:
    """quotaId 等に "PerModel" が含まれるか判定する（大文字小文字不問）。

    2026-07-26、書籍モード章並列化の実走行検証で実際に踏んだ429の quotaId が
    `GenerateContentInputTokensPerModelPerMinute-FreeTier`、quotaDimensions に
    `{'model': 'gemini-3.1-flash-lite'}` が明示されていることを確認した。これは、この
    TPM（入力トークン毎分）クォータがプロジェクト全体・モデル間で共有されているのではなく
    **モデル単位で独立集計されている**ことを示す直接証拠である。§7/§9 が採用していた
    「無料枠Liteプールの2モデルはTPM枠を共有している（`gemini_models.md`の実測で上限値が
    同一の250,000だったため）」という前提は、"上限の値が同じ"と"集計カウンターを共有する"
    を混同していた可能性が高く、この実データはその前提を覆す（docs/model_optimization.md
    §9 の記述訂正・troubleshooting_log 参照）。
    quotaId に "PerModel" という語が確認できる場合のみモデルローテーションを有効な回復策と
    みなす。確認できない場合（quotaId の形式が不明、503 で details が乏しい等）は、
    実証されていない前提で判断を変えるべきではないため、保守的に「不明」として扱う
    （呼び出し側は必要に応じて別途 quota_kind で判断する）。
    """
    return "permodel" in _quota_violation_text(exc)


def _extract_retry_delay_seconds(exc: BaseException) -> Optional[float]:
    """RetryInfo.retryDelay（例: "19s"）を抽出する。取れなければ None。

    構造化アクセス（APIError.details 経由）と、それが無い/合致しない場合の文字列
    マッチ（str(exc)。テストが投げる素の Exception/RuntimeError もこちらでカバーする）
    の両方を試す。旧実装が使っていた "retry in Xs" 形式（既存テストのフェイク例外）にも
    後方互換で対応する。
    """
    candidates = []
    details = getattr(exc, "details", None)
    if details is not None:
        try:
            candidates.append(json.dumps(details, ensure_ascii=False, default=str))
        except Exception:
            candidates.append(str(details))
    candidates.append(str(exc))

    for text in candidates:
        m = re.search(r'retryDelay["\']?\s*[:=]\s*["\']?(\d+(?:\.\d+)?)\s*s', text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    for text in candidates:
        m = re.search(r"retry in ([\d.]+)s", text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return None


def _lane_cooldown_seconds(exc: BaseException, quota_kind: str) -> float:
    """429/503を検知したレーンをどれだけクールダウンさせるかを決定する。

    - RPD起因: 次の太平洋時間深夜までの残り秒数（下限1時間・上限24時間）。retryDelayは
      不正確という報告があるため参考にせず、日次リセットの計算値をそのまま使う。
    - RPM/TPM起因: 1分前後で回復するため既定60秒。retryDelayが取れれば
      1〜120秒にクランプして使う（回復スケールを大きく超える値は信用しない）。
    - quotaMetric不明（503含む）: 安全側で既定30秒。retryDelayが取れれば同様にクランプ。
    """
    if quota_kind == "rpd":
        floor = _seconds_until_next_pacific_midnight()
        return min(max(floor, COOLDOWN_RPD_MIN_SECONDS), COOLDOWN_RPD_MAX_SECONDS)

    base = COOLDOWN_RPM_TPM_SECONDS if quota_kind in ("rpm", "tpm") else COOLDOWN_UNKNOWN_SECONDS
    retry_delay = _extract_retry_delay_seconds(exc)
    if retry_delay is not None:
        return max(COOLDOWN_MIN_SECONDS, min(retry_delay, RETRY_DELAY_CLAMP_MAX_SECONDS))
    return base


def _record_lane_cooldown(exc: BaseException, api_key: Optional[str], model: Optional[str]) -> str:
    """429/503 を検知したレーンをクールダウンレジストリに記録し、判定した quota_kind を返す。"""
    quota_kind = _classify_quota_violation(exc)
    seconds = _lane_cooldown_seconds(exc, quota_kind)
    lane_cooldown.mark(api_key, model, seconds)
    return quota_kind


def pick_lane(pool: List[str], keys: List[str], rr_index: int) -> Optional[Tuple[Optional[str], str]]:
    """クールダウン中でないレーンを、docs/model_optimization.md §8 のラウンドロビン順序を
    維持したまま選ぶ（ParallelTranslator._pick_batch_target / OCRManager._pick_page_target
    の共通ロジック、重複実装による分岐を避けるためここに集約する）。

    候補は i を通し番号として key = keys[i % K]（K=len(keys)。keysが空ならkey=None、
    モデル軸のみのRR）、model = pool[(i // max(K,1)) % M] という §8 と同じ式。rr_index
    から候補を順に辿り、クールダウン中でない最初のレーンを返す。K*M 回探しても全滅なら
    None を返す（例外を投げない・無限ループしない）— 呼び出し元はこの場合、クールダウンを
    無視した §8 従来どおりの割り当てにフォールバックすること。
    """
    if not pool:
        return None
    K = len(keys)
    M = len(pool)
    total_lanes = max(K, 1) * M
    for offset in range(total_lanes):
        i = rr_index + offset
        key = keys[i % K] if K > 0 else None
        model = pool[(i // max(K, 1)) % M]
        if not lane_cooldown.is_cooling(key, model):
            return key, model
    return None


# Gemini クライアントのキャッシュ（スレッドごとに独立した辞書）。
# 値は (client, 生成時のイベントループ) のペア。genai.Client の非同期トランスポートは
# 生成時のループに紐付くため、ループが変わったら再生成が必要（下記 _get_client 参照）。
_clients_local = threading.local()

def _get_clients_dict() -> Dict[str, tuple]:
    if not hasattr(_clients_local, "clients"):
        _clients_local.clients = {}
    return _clients_local.clients

def _get_client(api_key: str | None = None) -> genai.Client:
    """APIキーごとにクライアントをキャッシュして提供する（呼び出しスレッドごとに独立）。

    reset_pipeline_state() はパイプライン開始時にこのキャッシュを丸ごとクリアするが、
    それだけでは不十分だった: 1回の run_pipeline() 内でも Phase 1 (VLM ingestion) と
    Phase 4 (translation) はそれぞれ独立して run_async()/asyncio.run() を呼ぶため、
    別々のイベントループを持つ。Phase 1 で生成・キャッシュされたクライアントを Phase 4 の
    新しいループでそのまま再利用すると、非同期トランスポートが前のループ（既に閉じている）
    に紐付いたままのため初回呼び出しが "RuntimeError: Event loop is closed" になる
    （2026-07-21、書籍モードで章ごとに毎回発生することを確認）。
    そのため、カレントループが生成時のループと異なる場合はキャッシュを破棄して再生成する。
    同期呼び出し（ループなしのコンテキスト）はループ不問で既存クライアントを再利用する。
    """
    key = api_key or GEMINI_API_KEY
    if not key:
        raise ValueError("GEMINI_API_KEY (または GOOGLE_API_KEY) がセットされていません。.env ファイルを確認してください。")

    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    clients = _get_clients_dict()
    cached = clients.get(key)
    if cached is not None:
        cached_client, cached_loop = cached
        if current_loop is None or cached_loop is current_loop:
            return cached_client

    client = genai.Client(api_key=key)
    clients[key] = (client, current_loop)
    return client


def run_async(coro):
    """環境（Jupyter, FastAPI等）に応じた解決策で非同期処理を実行する。"""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # すでにループが走っている場合
            try:
                import nest_asyncio
                nest_asyncio.apply()
                return asyncio.run(coro)
            except ImportError:
                return loop.run_until_complete(coro)
    except RuntimeError:
        # ループが走っていない通常環境
        return asyncio.run(coro)



# ファイル書き込み時のヘッダー競合を防ぐためのグローバルロック
_METRICS_LOCK = threading.Lock()

def _dump_debug_prompt(prompt: Any, log_dir: Optional[Any], metrics_metadata: Optional[dict], is_async: bool = False):
    """プロンプトをデバッグファイルにダンプする。"""
    try:
        debug_id = "default_async" if is_async else "default"
        if metrics_metadata and isinstance(metrics_metadata, dict):
            m = metrics_metadata
            prefix = "async_" if is_async else ""
            debug_id = f"{prefix}{m.get('section', 'unknown')}_{m.get('batch_id', 'unknown')}"
        
        target_dir = log_dir if log_dir else STATE_DIR
        debug_path = target_dir / f"debug_prompt_{debug_id}.txt"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(str(prompt))
    except:
        pass

def _build_gemini_config(model: str, thinking_level: Optional[str], **kwargs) -> types.GenerateContentConfig:
    """Gemini API 公開用 Config オブジェクトを構築する。"""
    config_kwargs: dict[str, Any] = {
        "max_output_tokens": kwargs.get("max_output_tokens", 65536),
        "temperature": kwargs.get("temperature", 0.3),
    }
    if kwargs.get("response_mime_type"):
        config_kwargs["response_mime_type"] = kwargs["response_mime_type"]
    if kwargs.get("response_schema"):
        config_kwargs["response_schema"] = kwargs["response_schema"]

    # Gemini 3.x 以上のFlash/Lite および Thinkingモデルで有効
    if model and ("gemini-3" in model.lower() or "thinking" in model.lower()):
        level = thinking_level or "High"
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=level.upper())

    safety_settings = [
        types.SafetySetting(category=cat, threshold="BLOCK_NONE")
        for cat in [
            "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_DANGEROUS_CONTENT",
        ]
    ]
    return types.GenerateContentConfig(safety_settings=safety_settings, **config_kwargs)

def _log_metrics(metrics_metadata: dict, prompt_len: int, p_tokens: int, c_tokens: int, ttft: float, tps: float, duration: float):
    """計測されたメトリクスを CSV に記録する。"""
    try:
        section = metrics_metadata.get("section", "N/A")
        batch_id = metrics_metadata.get("batch_id", "N/A")
        csv_path = Path(metrics_metadata.get("csv_path", str(STATE_DIR / "ttft_metrics.csv")))
        
        with _METRICS_LOCK:
            file_exists = csv_path.exists()
            with open(csv_path, "a", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "section", "batch_id", "input_chars", "p_tokens", "c_tokens", "ttft", "tps", "duration"])
                writer.writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    section,
                    batch_id,
                    prompt_len,
                    p_tokens,
                    c_tokens,
                    f"{ttft:.3f}",
                    f"{tps:.1f}",
                    f"{duration:.2f}"
                ])
    except Exception as e_log:
        print_log(f"  [LLM] Metrics logging failed: {e_log}")


# キーローテーション直後の待機秒数（新しいプロジェクトの枠なのでクールダウン不要、短いジッターのみ）
ROTATION_RETRY_DELAY_BASE = 1.5

# モデルローテーション直後の待機秒数（同一キー内での切替のためクールダウン不要、短いジッターのみ）
MODEL_ROTATION_RETRY_DELAY_BASE = 1.0


def _maybe_restore_tier_after_rotation() -> None:
    """キーローテーションで有料キーへ切り替わった直後に呼ぶ。

    429/503 検知時の tier_manager.downgrade()（無条件・キー種別を問わない）は、無料キー内で
    ローテーションしただけ（free1→free2）なら正しい状態だが、有料キーへ切り替わった後も
    FREE のまま残ると、以降のリクエストが不必要に Lite モデル・縮小バッチで処理され続けて
    しまう（2026-07-21 レビュー指摘）。有料キーに切り替わった場合のみ PAID へ戻す。
    """
    if key_rotator.current_tier() == "paid":
        tier_manager.set_tier(GeminiTier.PAID)


def _calc_retry_wait(msg: str, attempt: int, retry_delay: float) -> tuple[float, bool]:
    """429/503 ならダウンシフトしてバックオフ秒数を計算する。戻り値: (wait_seconds, is_resource_limit)"""
    is_resource_limit = any(code in msg for code in ["429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE"])
    if is_resource_limit:
        tier_manager.downgrade()
        match = re.search(r"retry in ([\d\.]+)s", msg)
        base_wait = float(match.group(1)) + 1.0 if match else (10.0 * attempt)
        jitter = random.uniform(0, base_wait * 0.3)
        return base_wait + jitter, True
    return retry_delay * attempt, False


def call_gemini(
    prompt: str | list,
    model: str | None = None,
    api_key: str | None = None,
    max_output_tokens: int = 65536,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    thinking_level: str | None = None,
    max_retries: int = 5,
    retry_delay: float = 3.0,
    log_dir: Optional[Any] = None, # Pathオブジェクトを想定
    model_pinned: bool = False,
    key_pinned: bool = False,
    **kwargs,
) -> str:
    """
    Gemini API を同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。

    model_pinned: True の場合、呼び出し元が明示した model を ModelRotator の
    resolve()/advance() で上書きしない（Phase4のラウンドロビン割り当てなど、呼び出し元が
    プール内のどのモデルを使うか自分で管理したい場合に使う。通常の呼び出しは False のまま）。

    key_pinned: True の場合、呼び出し元が明示した api_key を KeyRotator.current()/advance()
    で上書きしない（model_pinned の完全な鏡写し。キー軸ラウンドロビンで意図的に free_2 を
    渡しても、上書きすると常に current() ＝ free_1 に引き戻されて分散が成立しないため）。
    プロアクティブな負荷分散（ラウンドロビン）とリアクティブな429フォールバックを混ぜない
    という §7/§8 の設計思想に従う。
    """
    use_default_model = (model is None)
    effective_key = api_key if key_pinned else (key_rotator.current() if key_rotator.is_configured() else api_key)
    client = _get_client(api_key=effective_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"))

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # モデルと Config の動的決定 (モデル未指定の場合のみリトライごとに再評価)
            current_model = model
            if use_default_model:
                current_model = get_default_model()
            # 無料枠Liteプールのメンバーなら現在のローテーション先へ差し替える
            # （model_pinned=True の場合は呼び出し元の指定を尊重してスキップ）
            if not model_pinned:
                current_model = model_rotator.resolve(current_model)

            config = _build_gemini_config(
                current_model, thinking_level, 
                max_output_tokens=max_output_tokens, 
                temperature=temperature,
                response_mime_type=response_mime_type,
                response_schema=response_schema
            )

            full_response_text = ""
            start_time = time.time()
            ttft = 0.0
            first_token_time = 0.0
            
            prompt_len = len(prompt) if isinstance(prompt, str) else len(str(prompt))
            print_log(f"  [LLM] API Request: {current_model} (Input: {prompt_len} chars, attempt: {attempt})")
            
            # ストリーミングによる計測
            response_stream = client.models.generate_content_stream(
                model=current_model,
                contents=prompt,
                config=config,
            )
            
            chunk = None
            for chunk in response_stream:
                if ttft == 0.0:
                    first_token_time = time.time()
                    ttft = first_token_time - start_time
                if hasattr(chunk, 'text') and chunk.text:
                    full_response_text += chunk.text
            
            # テキスト出力が空なら失敗として扱いリトライさせる。
            # chunk が返っていても finish_reason=MALFORMED_RESPONSE / MAX_TOKENS / SAFETY 等で
            # text が 0 トークンのことがあり、これを無言で "" として返すとレジュメ欠落など
            # 静かな品質劣化を招く（troubleshooting_log I-19）。空出力は常に異常とみなす。
            if not full_response_text:
                raise RuntimeError("APIから空のレスポンス（テキスト0トークン）が返されました。")

            end_time = time.time()
            duration = end_time - start_time
            gen_duration = (end_time - first_token_time) if first_token_time > 0 else 0
            
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = (usage.prompt_token_count or 0) if usage else 0
            c_tokens = (usage.candidates_token_count or 0) if usage else 0
            tps = c_tokens / gen_duration if gen_duration > 0 else 0

            print_log(f"  [LLM] Success: Duration {duration:.1f}s (TTFT: {ttft:.1f}s, TPS: {tps:.1f}, Prompt: {p_tokens}tk, Output: {c_tokens}tk)")
            
            # メトリクス記録
            _log_metrics(kwargs.get("metrics_metadata", {}), prompt_len, p_tokens, c_tokens, ttft, tps, duration)
                
            return full_response_text
            
        except Exception as e:
            last_error = e
            msg = str(e)
            print_log(f"  [LLM] リトライ {attempt}/{max_retries}: {type(e).__name__}: {msg}")

            if attempt < max_retries:
                wait_time, is_resource_limit = _calc_retry_wait(msg, attempt, retry_delay)
                rotated = False
                quota_kind = "unknown"
                tpm_blocks_model_rotation = False
                if is_resource_limit:
                    # レーン（このキー・このモデル）をクールダウンに入れる（§9）。
                    quota_kind = _record_lane_cooldown(e, effective_key, current_model)
                    # TPM起因の429は、quotaIdに"PerModel"が確認できる場合はモデル単位で
                    # 独立集計されている（2026-07-26 実データで確認、§9訂正）ため、モデル
                    # ローテーションが有効な回復策になりうる。確認できない場合のみ、従来どおり
                    # モデルローテーションをスキップしてキー切替へ直行する（保守的デフォルト）。
                    tpm_blocks_model_rotation = quota_kind == "tpm" and not _is_model_scoped_quota(e)
                if (is_resource_limit and not model_pinned and not tpm_blocks_model_rotation
                        and model_rotator.is_pool_member(current_model) and model_rotator.has_next()):
                    new_model = model_rotator.advance()
                    wait_time = MODEL_ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                    rotated = True
                    print_log(f"  [LLM] モデルローテーション: {new_model} に切替")
                elif is_resource_limit and not key_pinned and key_rotator.is_configured():
                    old_key = effective_key
                    new_key = key_rotator.best_available(
                        lambda k: not lane_cooldown.is_cooling(k, current_model)
                    )
                    if new_key is not None and new_key != old_key:
                        effective_key = new_key
                        client = _get_client(api_key=new_key)
                        wait_time = ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                        rotated = True
                        _maybe_restore_tier_after_rotation()
                        model_rotator.reset()  # 新しいキー = 別プロジェクトの独立枠なのでプール先頭から再開させる
                        print_log(f"  [LLM] キーローテーション: {key_rotator.index + 1}/{key_rotator.count} 番目のキーに切替")
                if is_resource_limit and not rotated:
                    print_log(f"  [LLM] リソース制限/混雑(503/429)を検知。ダウンシフトして{wait_time:.1f}秒待機...")
                time.sleep(wait_time)
    raise RuntimeError(f"Gemini API 呼び出し失敗: {last_error}")


async def call_gemini_async(
    prompt: str | list,
    model: str | None = None,
    api_key: str | None = None,
    max_output_tokens: int = 65536,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    thinking_level: str | None = None,
    max_retries: int = 5,
    retry_delay: float = 3.0,
    log_dir: Optional[Any] = None, # Pathオブジェクトを想定
    model_pinned: bool = False,
    key_pinned: bool = False,
    **kwargs,
) -> str:
    """
    Gemini API を非同期ストリーミングで呼び出し、TTFT/TPS 等を計測する。

    model_pinned: True の場合、呼び出し元が明示した model を ModelRotator の
    resolve()/advance() で上書きしない（Phase4のラウンドロビン割り当てなど、呼び出し元が
    プール内のどのモデルを使うか自分で管理したい場合に使う。通常の呼び出しは False のまま）。

    key_pinned: True の場合、呼び出し元が明示した api_key を KeyRotator.current()/advance()
    で上書きしない（model_pinned の完全な鏡写し。詳細は call_gemini の docstring 参照）。
    """
    use_default_model = (model is None)
    effective_key = api_key if key_pinned else (key_rotator.current() if key_rotator.is_configured() else api_key)
    client = _get_client(api_key=effective_key)

    # デバッグプロンプトのダンプ
    _dump_debug_prompt(prompt, log_dir, kwargs.get("metrics_metadata"), is_async=True)

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            # モデルと Config の動力決定 (モデル未指定の場合のみリトライごとに再評価)
            current_model = model
            if use_default_model:
                current_model = get_default_model()
            # 無料枠Liteプールのメンバーなら現在のローテーション先へ差し替える
            # （model_pinned=True の場合は呼び出し元の指定を尊重してスキップ）
            if not model_pinned:
                current_model = model_rotator.resolve(current_model)

            config = _build_gemini_config(
                current_model, thinking_level, 
                max_output_tokens=max_output_tokens, 
                temperature=temperature,
                response_mime_type=response_mime_type,
                response_schema=response_schema
            )

            full_response_text = ""
            start_time = time.time()
            ttft = 0.0
            first_token_time = 0.0
            
            prompt_len = len(prompt) if isinstance(prompt, str) else len(str(prompt))
            print_log(f"  [LLM async] API Request | Model: {current_model} | Chars: {prompt_len} | Thinking: {thinking_level} | Attempt: {attempt}")
            
            try:
                async with asyncio.timeout(600):
                    stream_gen = await client.aio.models.generate_content_stream(
                        model=current_model,
                        contents=prompt,
                        config=config,
                    )
                    
                    chunk = None
                    async for chunk in stream_gen:
                        if ttft == 0.0:
                            first_token_time = time.time()
                            ttft = first_token_time - start_time
                        if hasattr(chunk, 'text') and chunk.text:
                            full_response_text += chunk.text
            except asyncio.TimeoutError:
                raise RuntimeError(f"Gemini API 応答タイムアウト (600秒超過)")
            
            # テキスト出力が空なら失敗として扱いリトライさせる。
            # chunk が返っていても finish_reason=MALFORMED_RESPONSE / MAX_TOKENS / SAFETY 等で
            # text が 0 トークンのことがあり、これを無言で "" として返すとレジュメ欠落など
            # 静かな品質劣化を招く（troubleshooting_log I-19）。空出力は常に異常とみなす。
            if not full_response_text:
                raise RuntimeError("APIから空のレスポンス（テキスト0トークン）が返されました。")

            end_time = time.time()
            duration = end_time - start_time
            gen_duration = (end_time - first_token_time) if first_token_time > 0 else 0
            
            usage = getattr(chunk, 'usage_metadata', None)
            p_tokens = (usage.prompt_token_count or 0) if usage else 0
            c_tokens = (usage.candidates_token_count or 0) if usage else 0
            tps = c_tokens / gen_duration if gen_duration > 0 else 0

            print_log(f"  [LLM async] Success: Duration {duration:.1f}s (TTFT: {ttft:.1f}s, TPS: {tps:.1f}, Prompt: {p_tokens}tk, Output: {c_tokens}tk)")
            
            # メトリクス記録
            _log_metrics(kwargs.get("metrics_metadata", {}), prompt_len, p_tokens, c_tokens, ttft, tps, duration)
            
            return full_response_text
            
        except Exception as e:
            last_error = e
            msg = str(e)
            print_log(f"  [LLM async] リトライ {attempt}/{max_retries}: {type(e).__name__}: {msg}")

            if attempt < max_retries:
                wait_time, is_resource_limit = _calc_retry_wait(msg, attempt, retry_delay)
                rotated = False
                quota_kind = "unknown"
                tpm_blocks_model_rotation = False
                if is_resource_limit:
                    # レーン（このキー・このモデル）をクールダウンに入れる（§9）。
                    quota_kind = _record_lane_cooldown(e, effective_key, current_model)
                    # TPM起因の429は、quotaIdに"PerModel"が確認できる場合はモデル単位で
                    # 独立集計されている（2026-07-26 実データで確認、§9訂正）ため、モデル
                    # ローテーションが有効な回復策になりうる。確認できない場合のみ、従来どおり
                    # モデルローテーションをスキップしてキー切替へ直行する（保守的デフォルト）。
                    tpm_blocks_model_rotation = quota_kind == "tpm" and not _is_model_scoped_quota(e)
                if (is_resource_limit and not model_pinned and not tpm_blocks_model_rotation
                        and model_rotator.is_pool_member(current_model) and model_rotator.has_next()):
                    new_model = model_rotator.advance()
                    wait_time = MODEL_ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                    rotated = True
                    print_log(f"  [LLM async] モデルローテーション: {new_model} に切替")
                elif is_resource_limit and not key_pinned and key_rotator.is_configured():
                    old_key = effective_key
                    new_key = key_rotator.best_available(
                        lambda k: not lane_cooldown.is_cooling(k, current_model)
                    )
                    if new_key is not None and new_key != old_key:
                        effective_key = new_key
                        client = _get_client(api_key=new_key)
                        wait_time = ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
                        rotated = True
                        _maybe_restore_tier_after_rotation()
                        model_rotator.reset()  # 新しいキー = 別プロジェクトの独立枠なのでプール先頭から再開させる
                        print_log(f"  [LLM async] キーローテーション: {key_rotator.index + 1}/{key_rotator.count} 番目のキーに切替")
                if is_resource_limit and not rotated:
                    print_log(f"  [LLM async] リソース制限/混雑(503/429)を検知。ダウンシフトして{wait_time:.1f}秒待機...")
                await asyncio.sleep(wait_time)

    raise RuntimeError(f"Gemini API 非同期呼び出し失敗: {last_error}")


# --- 高レベル API ラッパー ---

async def translate_batch(
    chunks: List[dict],
    glossary_content: str,
    previous_translation: str,
    prompt_template: str,
    resume_content: str,
    section_name: str,
    api_key: str | None = None,
    expertise: str = "文化人類学",
    model: str | None = None,
    thinking_level: str = "High",
    state: Any = None,
    rate_limiter: AsyncLimiter = None,
    log_dir: Optional[Any] = None,
    max_parse_retries: int = 2,
    model_pinned: bool = False,
    key_pinned: bool = False,
) -> List["TreeNode"]:
    """チャンク群を XML タグ形式で翻訳し、パース失敗時は再試行する。"""
    from .models import TreeNode

    # 対象 ID セット（パース時の高速フィルタ用）
    known_ids = {str(c['id']) for c in chunks}

    def _parse_response(response: str) -> dict:
        """
        レスポンスから <p2w_chunk_ID> タグを一括抽出する（O(N) 一括パース）。

        戦略:
          Step A: 開始タグ → 次の開始タグ or 末尾 まで非欲張りマッチ
          Step B: 閉じタグが含まれていれば除去して内容を確定
          Step C: 3000文字超は末尾の誤取込み（閉じタグ欠落による複数チャンク混入）と見なし切り捨て
        """
        id_map = {}
        for m in re.finditer(
            r"<p2w_chunk_(\w+)>(.*?)(?=<p2w_chunk_\w+>|\Z)",
            response, re.DOTALL | re.IGNORECASE
        ):
            tag_id = m.group(1)
            if tag_id not in known_ids:
                continue
            content = m.group(2)
            # 閉じタグがあれば除去（その後の余分なテキストも含めて）
            content = re.sub(
                rf"</p2w_chunk_{re.escape(tag_id)}>.*\Z", "",
                content, flags=re.DOTALL | re.IGNORECASE
            ).strip()
            if not content:
                continue
            # Safety: 3000 文字超は閉じタグ欠落による複数チャンク誤取込みの可能性
            if len(content) > 3000:
                # 次の開始タグ手前で強制切断を試みる
                cut = re.split(r"<p2w_chunk_\w+>", content, maxsplit=1)
                content = cut[0].strip()
                print_log(f"  [LLM] ⚠️ chunk_{tag_id}: 抽出内容が3000文字超のため末尾を切り捨てました。")
                if not content:
                    continue
            id_map[tag_id] = content
        return id_map

    # 1. チャンクを XML タグ形式でパッケージング
    combined_text = ""
    for c in chunks:
        combined_text += f"<p2w_chunk_{c['id']}>\n{c['text']}\n</p2w_chunk_{c['id']}>\n\n"

    # 2. プロンプト構築
    base_prompt = prompt_template.format(
        expertise=expertise,
        glossary_content=glossary_content or "なし",
        resume_content=resume_content or "なし",
        previous_translation=previous_translation or "なし",
        chunk_json=combined_text
    )

    id_map = {}
    for attempt in range(max_parse_retries + 1):
        current_prompt = base_prompt
        if attempt > 0:
            # リトライ時: 欠落しているタグを明示してLLMに再挑戦させる
            missing_ids = [str(c['id']) for c in chunks if str(c['id']) not in id_map]
            missing_tags = ", ".join(f"<p2w_chunk_{i}>" for i in missing_ids)
            current_prompt += (
                f"\n\n(IMPORTANT) 前回の回答では以下のタグが正しく出力されませんでした: {missing_tags}\n"
                "今回は必ずすべての <p2w_chunk_ID> ... </p2w_chunk_ID> タグを完全な形で出力してください。"
            )

        async with rate_limiter:
            batch_id = "batch_" + str(chunks[0]['id']) if chunks else "unknown"
            metrics_meta = {"section": section_name, "batch_id": batch_id}
            response = await call_gemini_async(
                current_prompt, model=model, api_key=api_key, thinking_level=thinking_level,
                metrics_metadata=metrics_meta, log_dir=log_dir,
                model_pinned=model_pinned, key_pinned=key_pinned,
            )

        # 3. 一括パース
        id_map = _parse_response(response)

        missing_ids = [str(c['id']) for c in chunks if str(c['id']) not in id_map]
        if not missing_ids:
            print_log(f"  [LLM] バッチ翻訳成功: {len(id_map)}件すべてのタグを正常に抽出しました。")
            break
        else:
            if attempt < max_parse_retries:
                print_log(f"  [LLM] パース失敗 (欠落: {missing_ids})。再試行します ({attempt + 1}/{max_parse_retries})...")
                await asyncio.sleep(2.0)
            else:
                print_log(f"  [LLM] !!! 最大リトライ回数到達。欠落したまま継続します: {missing_ids} !!!")

    # 4. 結果の構築
    results = []
    for c in chunks:
        cid = str(c['id'])
        text = id_map.get(cid)
        if not text:
            text = f"【翻訳失敗】 {c['text']}"
        results.append(TreeNode(id=cid, text=text, role="p", seq_index=c.get("seq_index", 0.0)))

    return results


_limiters_local = threading.local()

def _get_limiters_dict() -> dict:
    if not hasattr(_limiters_local, "limiters"):
        _limiters_local.limiters = {}
    return _limiters_local.limiters


def reset_pipeline_state() -> None:
    """
    パイプライン開始前に呼び出す。
    AsyncLimiter・genai.Client の非同期トランスポートはいずれも生成時のイベントループに
    紐付くため、新しいパイプライン（新しい asyncio.run() ループ）が始まる前にキャッシュを
    クリアし、次回アクセス時に現在のループへ再生成させる。これを怠ると、前のパイプライン
    の（すでに閉じた）ループに紐付いたクライアントを再利用してしまい、非同期呼び出しの
    1回目が "RuntimeError: Event loop is closed" で失敗する。
    TierManager も paid にリセットして前回の downgrade 状態を引き継がないようにする。
    _CLIENTS・_CACHED_LIMITERS・TierManager はいずれもスレッドごとに独立しているため、
    ここでのクリア・リセットは呼び出したスレッド自身の状態にしか影響しない（Web側で複数の
    パイプラインが別スレッドで並行実行されていても、互いの状態を消し合わない）。
    """
    _get_limiters_dict().clear()
    _get_clients_dict().clear()
    tier_manager.set_tier(GeminiTier.PAID)
    model_rotator.reset()


def apply_tier_settings(tier: str | GeminiTier, api_key: str | None = None) -> Tuple[AsyncLimiter, dict]:
    """
    ティアに応じたレートリミッターと設定を返す。
    ティアの文字列表記を受け入れ、tier_manager のグローバル状態を更新する。
    api_key を渡すと (tier, api_key) 単位でレートリミッタを分離する（同一プロセス内で複数
    キーを切り替える場合や、Webアプリで複数キーを同時使用する場合に、キーごとに正しい
    残余レートを持たせるため）。省略時は従来通り tier のみでキーイングする。
    """
    if isinstance(tier, str):
        try:
            tier = GeminiTier(tier.lower())
        except ValueError:
            print_log(f"  [LLM] 警告: 未知のティア '{tier}'。PAID を使用します。")
            tier = GeminiTier.PAID

    # グローバルなティア状態を更新
    tier_manager.set_tier(tier)

    cache_key = tier if api_key is None else (tier, api_key)
    limiters = _get_limiters_dict()
    if tier == GeminiTier.FREE:
        settings = {"max_batch_chunks": 8, "max_batch_chars": 9000}
        if cache_key not in limiters:
            limiters[cache_key] = AsyncLimiter(1, 4.0)  # 1 request per 4 seconds
    else:
        settings = {"max_batch_chunks": 18, "max_batch_chars": 20000}
        if cache_key not in limiters:
            limiters[cache_key] = AsyncLimiter(100, 60.0)  # 100 requests per minute

    rate_limiter = limiters[cache_key]

    return rate_limiter, settings
