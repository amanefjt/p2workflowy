# [Knowledge Base] 構造マーカーの事前スキャン (Pre-scanner)

Phase 3 の開始直前に実行する。論文テキストの冒頭部分から主要な構造境界（Abstract, Keywords, Introduction 等）を正規表現で検知し、確定済みアンカーとして後段の Section Detector に渡す。

---

## 1. スキャン範囲の制限（Search Range）

処理の最適化と誤検知防止のため、テキスト全体ではなく、チャンク配列の **最初の30チャンク（`chunks[:30]`）のみ** を走査対象とする。

各チャンクについて、チャンク全体のテキスト（`text`）とその1行目（`first_line`）を判定に使用する。

---

## 2. 検知ルールと正規表現（Detection Rules）

以下の順番または並列で各チャンクに対して正規表現マッチングを行う。大文字・小文字は区別しない（`re.IGNORECASE`）。

### A. Keywords（キーワード）セクションの検知

- **対象**: チャンク全体（`text`）
- **正規表現**: `r"^(Keywords?|Key\s*words):"`
- **処理**:
  - マッチした場合、そのチャンクの `id` を `keywords_id` として記録する
  - **[重要・後方補完]** この時点で `abstract_start_id` が未定義だった場合、スキャン範囲の最初のチャンク（`search_range[0].id`）を `abstract_start_id` として強制的に記録する（Keywords の直前までが Abstract であるというヒューリスティクス）

### B. Introduction（本文の開始）の検知

- **対象**: チャンクの1行目（`first_line`）
- **正規表現**: `r"^([1I]\.?\s+)?Introduction"`
- **解説**: "Introduction" だけでなく、"1. Introduction" や "I. Introduction" といった表記揺れを許容する
- **処理**: マッチした場合、そのチャンクの `id` を `introduction_start_id` として記録する

### C. 著者メタデータ（Email アドレス）の検知

- **対象**: チャンク全体（`text`）
- **正規表現**: `r"[\w.-]+@[\w.-]+\.\w+"`
- **処理**: Email アドレスを含むチャンクの `id` を `metadata_ids` リストに追加する（後続処理で翻訳対象から除外するためのマーカー）

### D. 明示的な Abstract（抄録）の検知

- **対象**: チャンクの1行目（`first_line`）
- **正規表現**: `r"^Abstract$"`
- **処理**: 行が単一の "Abstract" という単語で構成されている場合、そのチャンクの `id` を `abstract_start_id` として記録する

---

## 3. スキャン結果の後段への受け渡し（Output）

検知した結果を Python の辞書オブジェクトとして返し、Phase 3 の Section Detector に「確定済みアンカー」として渡すこと。**LLM へのプロンプト注入は絶対に行わないこと。**

```python
# 返却する辞書の形式
{
    "abstract_start_id": int | str | None,
    "introduction_start_id": int | str | None,
    "keywords_id": int | str | None,
    "metadata_ids": List[int | str],
}
```