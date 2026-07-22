# 調査メモ: 無料枠1プロジェクト内での複数Liteモデル併用によるRPM/RPD拡張

**日付**: 2026-07-22
**ステータス**: **設計確定・実装未着手**（§6 の設計で実装に着手可能。§3 の未決論点は §6 冒頭で決定済み）
**位置づけ**: `docs/model_optimization.md` §6（複数APIキー運用）の続きにあたる着想。§6は「別GCPプロジェクトでキーを複数発行する」ことで枠を増やす設計だったが、本メモは**同一プロジェクト・同一キー内でも複数モデルを使い分ければ枠を実質拡張できるのでは**という別の切り口。

---

## 1. 着想のきっかけ

ユーザーが Google AI Studio の Rate Limit ダッシュボード（無料枠、プロジェクト `p2out1`、期間28日）のスクリーンショットを提示。モデルごとのRPM/RPD/TPMが**別々のカウンター**として表示されており、特に以下2点が同一の上限値を持ちながら別カウンターだった:

| モデル | RPM (使用/上限) | TPM (使用/上限) | RPD (使用/上限) |
|---|---|---|---|
| Gemini 3.1 Flash Lite | 5 / 15 | 14.03K / 250K | 26 / 500 |
| Gemini 3.5 Flash Lite | 0 / 15 | 0 / 250K | 0 / 500 |
| Gemini 3.5 Flash | 2 / 5 | 9.61K / 250K | 8 / 20 |
| Gemini 3.6 Flash | 0 / 5 | 0 / 250K | 0 / 20 |

`gemini-3.1-flash-lite` と `gemini-3.5-flash-lite` は上限が完全一致（`gemini_models.md` §4 の実測どおり）だが、**使用量は独立にカウントされている**。つまりこの2モデルを交互に使えば、1プロジェクト・1キーのままでも合算で RPM 30・RPD 1000 相当まで使える可能性がある（`gemini-3.5-flash`/`gemini-3.6-flash` の組も同様の関係）。

ユーザーからの補足: レジュメ担当の `DEFAULT_MODEL_RESUME`（今回のセッションで `gemini-3.6-flash` に切替済み、`docs/management/requirements_log.md` の 2026-07-22 エントリ参照）は据え置きで、**この案は Lite 系（`DEFAULT_MODEL`/`DEFAULT_MODEL_FREE`/`DEFAULT_MODEL_VLM` が使う無料ルート）だけを対象にする**という前提。

## 2. 現状のアーキテクチャ（`core/llm_client.py`）との関係

この案を実装するなら触ることになる既存の仕組み:

- **`TierManager`**（`core/llm_client.py:66`前後）: 429/503検知時に「モデルをLiteにダウンシフトする」だけの単純な状態機械（`current_tier`: PAID/FREE、`downgrade()`で一方向にFREEへ）。今回の案は「Lite同士で別モデルへ回す」という**新しい軸**であり、既存のPAID/FREE軸とは直交する。
- **`KeyRotator`**（`core/llm_client.py:124`前後）: `docs/model_optimization.md` §6 で導入済みの「キー単位のローテーション」。forward-only、429/503で次のキーへ進む。本案はこれと似た設計（forward-onlyローテーション）を「キー」でなく「モデル」に対して行うイメージになりそうだが、キーとモデルの2軸が両方ローテーションしうる状態になるとテスト・デバッグの複雑度が上がる点に注意。
- **`get_default_model(purpose)`**（`core/llm_client.py:35`）: `purpose="free"` のとき `DEFAULT_MODEL_FREE` を固定で返すだけの単純な参照。ここを「候補リストから選ぶ」形に変える必要がある。
- **`apply_tier_settings(tier, api_key=...)`**（`core/llm_client.py:706`前後）: `(tier, api_key)` でレートリミッタをキーイングしている。モデルを増やすなら `(tier, api_key, model)` あるいは `(model,)` 単位でリミッタを持つ必要が出てくる可能性がある。

## 3. 検討すべき論点（未決定・次セッションで詰める）

1. **切替のトリガー**: (a) リクエストごとにラウンドロビン、(b) 429/503を検知したときだけ次のモデルへ進む（`KeyRotator`と同じforward-only方式）、のどちらが良いか。(a)は素朴に枠を倍増できるが実装・検証コストが高い。(b)は実装が軽いが「片方が枯渇するまで倍増効果が出ない」ため、旧来の「Lite単体でRPD上限に達したら完走できない」問題への対策としては forward-only でも十分な可能性がある。
2. **翻訳品質の一貫性**: 1つの論文・書籍の処理中にモデルが `gemini-3.1-flash-lite` → `gemini-3.5-flash-lite` と切り替わると、同一文書内で訳文のトーン・用語選択の一貫性が変わるリスクがある。特にPhase 4翻訳（スライディングウィンドウで前訳を参照する設計）はモデルが変わると「前訳の癖」が変わる可能性があり、検証が必要（`docs/translation_review_checklist.md` でのA/B比較が候補）。
3. **thinking_level のデフォルト差**: `gemini_models.md` §3 によれば `gemini-3.1-flash-lite`（旧世代）はデフォルト `HIGH`、`gemini-3.5-flash-lite`はデフォルト`MINIMAL`。p2workflowyは各フェーズで`thinking_level`を明示指定しているため実害は薄いはずだが、切替実装時に「明示指定を必ず両モデルに通す」ことをテストで担保する必要がある。
4. **対象範囲**: `DEFAULT_MODEL_VLM`（Phase 1 OCR）・`DEFAULT_MODEL`/`DEFAULT_MODEL_FREE`（Phase 2 DNA・Phase 3構造ツリー・Phase 4翻訳）のどこまでを対象にするか。VLMは画像入力必須なので両モデルとも対応している前提の確認が要る（`gemini_models.md` によれば両方マルチモーダル対応のはず）。
5. **`gemini-3.5-flash`/`gemini-3.6-flash`のペアにも同じ手が使えるか**: レジュメは据え置き方針だが、将来的にPAID tierの `DEFAULT_MODEL` 側でも同様の複数モデル併用を検討する余地があるかは今回スコープ外として保留。
6. **実装場所**: `KeyRotator`を拡張するか、並列の新クラス（`ModelRotator`的な）を新設するか。`apply_tier_settings`のレートリミッタキーイングをどう拡張するかも含め、設計は未着手。

## 4. 次セッションで着手する場合の入口

このメモを読んで以下を確認・決定すればすぐ設計に入れる:

1. §3-1（切替トリガー）と §3-4（対象範囲）をまず決める。おすすめは「forward-onlyでまず`DEFAULT_MODEL_FREE`のみ対象にスコープを絞る」（既存`KeyRotator`と設計思想を揃えられ、差分が小さい）。
2. §3-2（品質一貫性）の懸念を許容するかどうかをユーザーに確認する（許容できないなら「文書単位で1モデルに固定し、文書間でのみローテーション」という設計に倒れる）。
3. 実装は `core/llm_client.py` の `KeyRotator` 周辺（124行目付近）を参考に、モデルローテーション用の新しい状態管理を設計する。

## 5. 次セッションを再開するときに言うべきこと

このセッションを引き継ぐには、次のように伝えれば十分:

> 「`docs/superpowers/specs/2026-07-22-free-tier-multi-model-lite-pool-design.md` を読んで、無料枠内でのLiteモデル併用ローテーションの設計を続けて」

このメモ単体で問題設定・現状アーキテクチャ・未決定論点・入口までが完結しているため、会話履歴を遡る必要はない。

---

## 6. 確定した設計（2026-07-22 セッション続き）

### 6.1 §3 の未決論点への決定

- **§3-1（切替トリガー）**: 推奨どおり forward-only（429/503 検知時のみ次の Lite モデルへ進む。`KeyRotator` と同じ設計思想）。ラウンドロビンは不採用。
- **§3-2（品質一貫性）**: **同一文書内でのモデル切替を許容する**（ユーザー確認済み）。理由: 現状は Lite 単体で RPD 枯渇 = そのまま失敗/停滞であり、切替を許容してもそれは「本来失敗していたはずのケースを完走させる」場合にのみ発生するため、既存挙動に対する正味の劣化ではなく改善。`reset_pipeline_state()` で毎回プール先頭モデルから再開する（後述 6.3）ため、切替は「本当に必要な時だけ」起きる。
- **§3-3（thinking_level のデフォルト差）**: 実害なしと確認。`_build_gemini_config()`（`core/llm_client.py:261`）は `model` に関わらず呼び出し側が指定した `thinking_level`（省略時 `"High"`）を常に `ThinkingConfig` へ明示投入するため、モデルのデフォルト差はこの経路では表面化しない。対応不要。
- **§3-4（対象範囲）**: `DEFAULT_MODEL_FREE` を対象とする。**`DEFAULT_MODEL_VLM` への副作用的な波及も許容する**（ユーザー確認済み）。理由: 後述 6.2 のプール判定は「解決済みモデル文字列がプールのメンバーか」で行う純粋な値ベース判定であり、`purpose` を意識しない。現状 `DEFAULT_MODEL_VLM == DEFAULT_MODEL_FREE == "gemini-3.1-flash-lite"` であるため、Phase 1 VLM OCR 呼び出し（`ocr_manager.py`, `pdf_splitter.py`）にも自然に波及する。`gemini_models.md` によれば両モデルともマルチモーダル対応のため実害はない想定。将来 `DEFAULT_MODEL_VLM` がプール外の値に変更されれば、この波及は自動的に消える（自己限定的で安全）。
- **§3-5（PAID tier ペア）**: 引き続きスコープ外（据え置き）。
- **§3-6（実装場所）**: `KeyRotator` を拡張するのではなく、並列の新クラス `ModelRotator` を新設する（6.2 参照）。理由: `KeyRotator` は CLI 専用のプロセスグローバル状態、`TierManager` は CLI/Web 両対応のスレッドローカル状態という使い分けが既にあり、`ModelRotator` は Web からも呼ばれうる（Web の無料枠パスコード経路も対象）ため `TierManager` 側の設計（スレッドローカル）を踏襲する方が既存の使い分けと整合する。

### 6.2 新規コンポーネント: `ModelRotator`（`core/llm_client.py`）

`TierManager` と同じく `threading.local()` を裏に持つシングルトン（forward-only の進め方は `KeyRotator` を踏襲）。

```python
class ModelRotator:
    """無料枠Liteモデルプール内でのフォワードオンリー・ローテーション（TierManagerと同じくスレッドローカル）。

    KeyRotatorがAPIキー単位のローテーションであるのに対し、こちらは同一キー・同一プロジェクト内で
    複数のLiteモデル（gemini-3.1-flash-lite / gemini-3.5-flash-lite 等）のRPD/RPMが別カウンターで
    管理されている性質を利用し、片方が429/503でリソース枯渇したときにもう片方へ切り替える。
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

model_rotator = ModelRotator()
```

設定値: `core/coreprompts.json` に `DEFAULT_MODEL_FREE_POOL` を追加。

```json
"DEFAULT_MODEL_FREE_POOL": ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"],
```

`DEFAULT_MODEL_FREE` は変更せず先頭要素として残す（後方互換: `DEFAULT_MODEL_FREE_POOL` が無ければ `[DEFAULT_MODEL_FREE]` の1要素プールにフォールバックし、`has_next()` が常に `False` になるため何も変わらない = 安全なデフォルト）。

### 6.3 呼び出し経路への組み込み（`call_gemini` / `call_gemini_async`）

**重要な発見**: `resolve()` を「`model` 引数が `None` のときだけ」通す設計では不十分。Phase1/2/3 の主要呼び出し元の多くは `model` を**コンストラクタ時点で一度だけ** `get_default_model()` で解決し、固定文字列として `call_gemini`/`call_gemini_async` に渡している（`pdf_splitter.py:45`, `ocr_manager.py:106`, `state_integrator.py:21`, `book_manager.py:89/92/195`）。これらの呼び出しではリトライループの `use_default_model` が `False` になるため、素朴な実装だと再試行のたびに `get_default_model()` が呼ばれず、ローテーションが一切効かない。

対策: `current_model` を**毎試行**（ループの毎 iteration）で `model_rotator.resolve()` に通す。渡された `model` が明示指定か既定値解決かに関わらず、「今の解決済みモデルがプールのメンバーかどうか」だけで判定するため、上記の固定モデル解決パターンでも正しく効く。

```python
# call_gemini / call_gemini_async の各 try ブロック冒頭（既存の current_model 決定直後）
current_model = model
if use_default_model:
    current_model = get_default_model()
current_model = model_rotator.resolve(current_model)  # ← 追加
```

except ブロックのリソース制限分岐（`_calc_retry_wait` で `is_resource_limit=True` が返った場合）に、モデルローテーションを**キーローテーションより優先**して挿入する:

```python
MODEL_ROTATION_RETRY_DELAY_BASE = 1.0  # モデル切替直後の待機秒数（キー切替と同様、短いジッターのみ）

# ... except 節内、is_resource_limit 判定の直後
rotated = False
if is_resource_limit and model_rotator.is_pool_member(current_model) and model_rotator.has_next():
    new_model = model_rotator.advance()
    wait_time = MODEL_ROTATION_RETRY_DELAY_BASE + random.uniform(0, 1.0)
    rotated = True
    print_log(f"  [LLM] モデルローテーション: {new_model} に切替")
elif is_resource_limit and key_rotator.is_configured() and key_rotator.has_next():
    # 既存のキーローテーション処理（そのまま）
    ...
    model_rotator.reset()  # 新しいキー = 別プロジェクトの独立枠なのでプール先頭から再開させる
if is_resource_limit and not rotated:
    # 既存のダウンシフト+待機処理（そのまま）
    ...
```

優先順位の理由: モデル切替は同一キー内で完結し、新しい `client` 生成もクールダウンも不要な最も軽い手段。プールを使い切って初めて（`has_next()` が `False` になって初めて）、CLI の複数キー設定（`key_rotator`）や従来の待機付きダウンシフトにフォールバックする。

`call_gemini` と `call_gemini_async` の両方に同一パターンで適用する（コードはほぼ相似形のため、修正も対になる）。

### 6.4 リセットとレートリミッタへの影響

- `reset_pipeline_state()`（`core/llm_client.py:688`）に `model_rotator.reset()` を追加。`tier_manager.set_tier(GeminiTier.PAID)` と同じ「毎パイプライン開始時に楽観的に初期状態へ戻す」思想を踏襲。
- `apply_tier_settings()` のレートリミッタ（`(tier, api_key)` でキーイング）は**変更不要**。このリミッタは RPD のようなサーバー側の絶対枠ではなく、クライアント側の RPM ペーシング用トークンバケットに過ぎない。モデルを切り替えても既存のリミッタ・ペースをそのまま使い続けて問題ない（実際の RPD 枯渇はサーバーから 429 で通知され、それが今回のローテーショントリガーそのもの）。
- **書籍モードでの per-chapter リセットについて**: `reset_pipeline_state()` は書籍モードでは章ごと（`run_pipeline()` 呼び出しごと）に呼ばれるため、`model_rotator` も章ごとにプール先頭へ戻る。これは容量拡張効果を損なわない（forward-only の `advance()` は各章内で 429 を機に即座に効くため、無駄になるのは「先頭モデルが枯渇していた場合の1回分の 429 往復」だけで、拒否された 429 リクエスト自体は RPD を消費しない）。また `tier_manager` も同じく章ごとに PAID へ楽観的リセットされる既存挙動と整合しており、新規のリスクではない。

### 6.5 テスト計画

`tests/unit/test_llm_client.py`（既存の `test_rotation_to_paid_key_restores_tier_to_paid` 等、`KeyRotator` 用テストと同じファイル・同じモッキング流儀）に追記:

1. `ModelRotator` 単体: `resolve()` がプール外モデルを素通しすること、`advance()` が forward-only で最終要素で止まること、`reset()` で index 0 に戻ること。
2. `call_gemini_async` 統合: プール先頭モデルで 429 を返すフェイクストリームを用意し、リトライ後の `current_model` が `DEFAULT_MODEL_FREE_POOL[1]` になること（`_dump_debug_prompt` 等のログ経由か、`client.aio.models.generate_content_stream` 呼び出し引数の `model=` をアサートする形）。
3. `model_rotator.is_pool_member()` が `False` を返すケース（PAID tier のモデルや `DEFAULT_MODEL_RESUME`）でローテーションが発火しないこと。

実装後は `golden-verification` skill で `--lite` モードの論文1本を completions し、構造・翻訳品質に回帰がないことを確認する（CLAUDE.md の完了宣言前チェックリスト）。

### 6.6 ドキュメント更新箇所（実装完了後）

- `docs/model_optimization.md`: 新セクションとして Lite プール併用の運用ロジックと 6.1 の品質トレードオフ判断を追記。
- `docs/management/requirements_log.md`: 仕様変更として本設計の採用理由を追記（CLAUDE.md の変更管理ルールに従う）。

### 6.7 設計レビュー（Opus, 2026-07-22）

§6 設計を Opus にコードベース照合込みでレビューさせた結果: **GO**（設計通り実装して問題なし）。§6.3 のリトライループ組み込みタイミング、§6.2 のスレッドローカル選択、§3-4 の VLM 波及の安全性、§6.5 の except 節スロットインの整合性をすべて確認済み。指摘は advisory 2件のみで、いずれも本メモに反映済み: (1) キーローテーション時に `model_rotator.reset()` も呼ぶ（§6.3 コード例に追加済み）、(2) 書籍モードの per-chapter リセットの扱いを明文化（§6.4 に追加済み）。

## 7. 次に実装するときの入口

このメモ（§6）で設計は確定しているため、次セッションでは以下をそのまま実装すればよい:

1. `core/coreprompts.json` に `DEFAULT_MODEL_FREE_POOL` を追加。
2. `core/llm_client.py` に `ModelRotator` クラスを追加し、`call_gemini`/`call_gemini_async` の2箇所（`current_model` 決定部・except節の resource_limit 分岐）に組み込み、`reset_pipeline_state()` に `model_rotator.reset()` を追加。
3. `tests/unit/test_llm_client.py` にテストを追加（6.5 参照）。
4. `--lite` モードで論文1本を実走させ `golden-verification` skill で検証。
5. `docs/model_optimization.md` と `docs/management/requirements_log.md` を更新。
