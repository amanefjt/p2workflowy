"""
p2workflowy V2 Phase 1: Ingest & Preprocess
indi_preprocessor.md の仕様に完全準拠したノイズ除去・整形モジュール。
"""

import re
import csv
import statistics
from pathlib import Path
from typing import List

import wordninja

from .models import RawChunk, save_chunks_to_json
from .config import load_glossary_csv, print_log


# ============================================================
# 1. 初期クレンジング (Basic Cleansing)
# ============================================================

def normalize_line_endings(text: str) -> str:
    """改行コードの正規化: \\r\\n / \\r → \\n"""
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    return text


def rejoin_hyphenated_words(text: str) -> str:
    """行またぎのハイフン結合: 'word-\\nword' → 'wordword'"""
    return re.sub(r"(\w)-\n(\w)", r"\1\2", text)


# ============================================================
# 2. フォーマット自動判定 (Format Detection)
# ============================================================

def detect_format(text: str) -> str:
    """
    PDF テキスト抽出フォーマットの判定。
    戻り値: 'one_line_per_paragraph' or 'wrapped_paragraphs'
    """
    lines = text.split("\n")
    non_empty_lines = [line for line in lines if line.strip()]

    if not non_empty_lines:
        return "one_line_per_paragraph"

    # 空行（\\n\\s*\\n）の存在チェック
    has_blank_lines = bool(re.search(r"\n\s*\n", text))

    # 全行の文字数の中央値を計算
    line_lengths = [len(line) for line in non_empty_lines]
    median_length = statistics.median(line_lengths)

    # 判定
    if median_length >= 100 or not has_blank_lines:
        return "one_line_per_paragraph"
    else:
        return "wrapped_paragraphs"


# ============================================================
# 3. 段落の再結合 (Smart Unwrap Heuristics)
# ============================================================

# 文末判定用正規表現（引用ブラケット対応）
_SENTENCE_END_RE = re.compile(r"""[.!?;:\"'](?:\[[\d,\s-]+\])?\s*$""")

# Trailing words リスト
_TRAILING_WORDS = frozenset([
    "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "by", "from", "as", "is",
    "was", "were", "are", "has", "had", "have", "that",
    "which", "who", "whom", "this", "these", "those",
])


def _should_keep_break(current_line: str, next_line: str) -> bool:
    """
    現在の行と次の行の間の改行を維持するかどうかを判定する。
    True = 改行維持（結合しない）, False = 結合する
    """
    current_stripped = current_line.rstrip()
    next_stripped = next_line.strip()

    if not current_stripped or not next_stripped:
        return True

    # 結合しない条件 1: 次の行がインデント（スペース2つ以上）で始まっている
    if next_line.startswith("  "):
        return True

    # 結合しない条件 2: 文末判定（引用対応）
    if _SENTENCE_END_RE.search(current_stripped):
        # ただし、次の行が小文字で始まる場合は結合する（文が続いている）
        if next_stripped and next_stripped[0].islower():
            return False
        return True

    # 結合する条件 1: ハイフン末尾
    if current_stripped.endswith("-"):
        return False

    # 結合する条件 2: 次の行の先頭が小文字
    if next_stripped and next_stripped[0].islower():
        return False

    # 結合する条件 3: カンマ末尾
    if current_stripped.endswith(","):
        return False

    # 結合する条件 4: Trailing words
    last_word = current_stripped.split()[-1].lower().rstrip(".,;:!?")
    if last_word in _TRAILING_WORDS:
        return False

    # デフォルト: 改行維持
    return True


def smart_unwrap(text: str) -> List[str]:
    """
    wrapped_paragraphs フォーマットのテキストを段落に再結合する。
    戻り値: 段落のリスト
    """
    lines = text.split("\n")
    paragraphs: List[str] = []
    current_paragraph_parts: List[str] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 空行 = 段落区切り
        if not stripped:
            if current_paragraph_parts:
                paragraphs.append(" ".join(current_paragraph_parts))
                current_paragraph_parts = []
            continue

        if not current_paragraph_parts:
            current_paragraph_parts.append(stripped)
        else:
            # 前の行との結合判定
            prev_line = current_paragraph_parts[-1]
            if _should_keep_break(prev_line, line):
                # 改行維持 → 新しい段落開始
                paragraphs.append(" ".join(current_paragraph_parts))
                current_paragraph_parts = [stripped]
            else:
                # ハイフン末尾の場合はハイフンを除去して結合
                if prev_line.endswith("-"):
                    current_paragraph_parts[-1] = prev_line[:-1]
                current_paragraph_parts.append(stripped)

    # 最後の段落を追加
    if current_paragraph_parts:
        paragraphs.append(" ".join(current_paragraph_parts))

    return paragraphs


def split_one_line_per_paragraph(text: str) -> List[str]:
    """
    one_line_per_paragraph フォーマットの場合、各行をそのまま段落として扱う。
    """
    lines = text.split("\n")
    paragraphs = [line.strip() for line in lines if line.strip()]
    return paragraphs


# ============================================================
# 4. チャンク化とフィルタリング (Chunking & Filtering)
# ============================================================

# ページ番号の正規表現
_PAGE_NUMBER_RE = re.compile(r"^\d{1,5}$")


def filter_paragraphs(paragraphs: List[str]) -> List[str]:
    """段落を strip し、ページ番号行を除去する。"""
    filtered = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # ページ番号の除去
        if _PAGE_NUMBER_RE.match(p):
            continue
        filtered.append(p)
    return filtered


# ============================================================
# 5. 用語保護付き単語分割 (Glossary-Aware wordninja)
# ============================================================

# 癒着単語の検出: 20文字以上のアルファベット連続
_LONG_WORD_RE = re.compile(r"[A-Za-z]{20,}")


def glossary_aware_word_split(text: str, glossary_keys: List[str]) -> str:
    """
    用語保護付きの単語分割を実行する。
    1. glossary のキーをプレースホルダーに退避
    2. 長い癒着単語に wordninja.split() を適用
    3. プレースホルダーを復元
    """
    # ステップ 1: 退避（長い用語から順に処理して、部分一致の問題を防ぐ）
    placeholders: dict[str, str] = {}
    sorted_keys = sorted(glossary_keys, key=len, reverse=True)

    for idx, key in enumerate(sorted_keys):
        placeholder = f"__GLOS_{idx}__"
        if key in text:
            text = text.replace(key, placeholder)
            placeholders[placeholder] = key

    # ステップ 2: 癒着単語を wordninja で分割
    def _split_long_word(match: re.Match) -> str:
        word = match.group(0)
        # プレースホルダーの一部なら無視
        if word.startswith("GLOS") or word.endswith("GLOS"):
            return word
        split_result = wordninja.split(word)
        if len(split_result) > 1:
            return " ".join(split_result)
        return word

    text = _LONG_WORD_RE.sub(_split_long_word, text)

    # ステップ 3: 復元
    for placeholder, original in placeholders.items():
        text = text.replace(placeholder, original)

    return text


# ============================================================
# メイン実行関数
# ============================================================

def run_phase1(
    input_path: str,
    state_path: str | Path,
    glossary_path: str | None = None,
    save_state: bool = True,
) -> List[RawChunk]:
    """
    Phase 1 メイン処理: テキストファイルを読み込み、ノイズ除去・整形を行う。

    Args:
        input_path: 入力テキストファイルのパス
        state_path: 保存先の JSON パス
        glossary_path: glossary.csv のパス（省略時はデフォルト）
        save_state: state/phase1_clean.json に保存するか

    Returns:
        List[RawChunk]: クレンジング済みチャンクのリスト
    """
    # テキスト読み込み
    path = Path(input_path)
    raw_text = path.read_text(encoding="utf-8")

    # 1. 初期クレンジング
    text = normalize_line_endings(raw_text)
    text = rejoin_hyphenated_words(text)

    # 2. フォーマット自動判定
    fmt = detect_format(text)
    print_log(f"  [Phase 1] フォーマット判定: {fmt}")

    # 3. 段落化
    if fmt == "wrapped_paragraphs":
        paragraphs = smart_unwrap(text)
    else:
        paragraphs = split_one_line_per_paragraph(text)

    # 4. フィルタリング
    paragraphs = filter_paragraphs(paragraphs)
    print_log(f"  [Phase 1] フィルタ後段落数: {len(paragraphs)}")

    # 5. Glossary-Aware wordninja
    glossary = load_glossary_csv(glossary_path)
    glossary_keys = list(glossary.keys())

    processed_paragraphs = []
    for p in paragraphs:
        processed = glossary_aware_word_split(p, glossary_keys)
        processed_paragraphs.append(processed)
    paragraphs = processed_paragraphs
    print_log(f"  [Phase 1] wordninja 処理完了")

    # RawChunk リストの構築
    chunks: List[RawChunk] = []
    for idx, para in enumerate(paragraphs):
        chunk = RawChunk(
            id=idx,
            text=para,
            seq_index=float(idx),
        )
        chunks.append(chunk)

    print_log(f"  [Phase 1] 最終チャンク数: {len(chunks)}")

    # State 保存
    if save_state:
        save_chunks_to_json(chunks, str(state_path))
        print_log(f"  [Phase 1] State 保存: {state_path}")

    return chunks
