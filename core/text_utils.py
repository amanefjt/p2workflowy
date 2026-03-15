import re

# 文末判定用正規表現（引用ブラケット対応）
# Phase 1, Phase 3 で共有
_SENTENCE_END_RE = re.compile(r"""[.!?;:\"'](?:\[[\d,\s-]+\])?\s*$""")

# Trailing words リスト（前置詞・冠詞等、行末にある場合に結合を促す単語群）
# Phase 1, Phase 3 で共有
_TRAILING_WORDS = frozenset([
    "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "by", "from", "as", "is",
    "was", "were", "are", "has", "had", "have", "that",
    "which", "who", "whom", "this", "these", "those",
])
