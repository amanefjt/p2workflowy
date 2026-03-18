# Remove Inline Running Headers - Evaluation & Design

## 概要 (Overview)
ユーザーより提供された `remove_inline_running_headers` の実装と関連テストに対する4つのフィードバックを評価し、コードベースの現状（V3.1+）の設計と照らし合わせた結果と、今後の修正方針（Design）を以下に定義します。

---

## 1. 空行によるインデックスずれ (Empty Line Index Shifting)
*   **物理的挙動の修正**: インデックスは「非空行の連番 (`non_empty_idx`)」としてカウントするロジックに変更し、真のテキストの先頭2行を正確に評価対象とします。

## 2. 正規表現の末尾 `\s+` 問題と独立行の処理
*   **フィードバック内容**: 末尾の `\s+` が必須だと、ヘッダーが独立した行として存在する場合にマッチしない。また、独立行の場合は行ごと削除する必要がある。
*   **方針 (Design)**: 
    *   正規表現末尾を `(\s+|$)` に変更し、行末も許容する。
    *   `remaining_text` が空（独立行）の場合は `line_processed = ""` とし、行を完全削除する。
    *   `remaining_text` が小文字開始（癒着行）の場合は `line_processed = remaining_text` とし、本文断片を残す。
    *   大文字開始（章タイトル等）の場合は従来どおり保護する。

## 2. 章タイトルとランニングヘッドの構造的曖昧性 (Structural Ambiguity)
*   **フィードバック内容**: 現在の正規表現は `KEYWORD [PAGE_NUM]` と `[PAGE_NUM] KEYWORD` の両方にマッチする。前者はランニングヘッダーだが、後者は章タイトルであるため、「キーワードの前か後ろか」で正規表現を分離すべきとの指摘。
*   **評価**: **不採用 (Flawed / Partially Correct)**
    *   **事実誤認**: 学術書など見開きページのあるPDFでは、**偶数ページ（左側）の柱は `[PAGE_NUM] KEYWORD` の順序になります（例: "17 The Ethnographic Effect I"）**。したがって、このパターンを正規表現から外すと、偶数ページの柱が一切除去されなくなる重篤なリグレッションが発生します。
    *   **現在の設計の優位性**: 既存の実装では、章タイトル（`[PAGE_NUM] KEYWORD [Subtitle]`）と、本文に癒着した左側柱（`[PAGE_NUM] KEYWORD [本文の続き...]`）を区別するために、**「残存テキストの先頭文字が小文字か大文字か (`remaining_text[0].islower()`)」** をガードとして使用しています。
        *   章の副題（Subtitle）は大文字で始まります。
        *   本文の癒着（パラグラフの途中からの再開）は小文字で始まります。
    *   このガード（V3.1で導入済）が極めて堅牢に機能しているため、正規表現自体の構造（前後）を制限する必要はありません（むしろ制限してはならない）。
*   **方針 (Design)**: 正規表現パターン `rf"^({escaped_kw}\s*\d+|\d+\s*{escaped_kw})\s+"` は両面ページに対応する不可欠な仕様として**維持**し、`islower()` ガードも引き続き信頼します。

## 3. TOC ガードの脆弱性 (TOC Guard Vulnerability)
*   **フィードバック内容**: 前の行が "Table of Contents" かどうかを判定する局所的な1行チェックは脆弱である。
*   **評価**: **対象外 (Irrelevant / Context Missing)**
    *   フィードバック提供者はコードの全体像を見ておらず、想像で推測しています。
    *   現在の `p2workflowy` V2.9.x 以降のコードベースでは、`remove_inline_running_headers` の内部に "Table of Contents" という直前行チェックは存在しません。
    *   TOCの保護は、上位関数 `extract_text_fast` に追加された **「ページ上下 15% の位置制約 (Positional Margin Filtering)」** と、`phase1_preprocess.py` の **「ネガティブ・ルックアヘッド (Negative Lookahead Protection)」** という堅牢な二段構えのシステムで既に解決済みです。
*   **方針 (Design)**: 既存の強力な自律的TOC保護システムが稼働しているため、この関数内でのTOCガードに関する追加修正は行いません。

## 4. テストのアサーション不足 (Missing Assertions in test_debug_header2)
*   **フィードバック内容**: `test_debug_header2.py` が `print` のみで `assert` を持っていない。
*   **評価**: **妥当 (Valid) / 修正必須**
    *   これまでは挙動探索用のスクリプトでしたが、仕様が固まった（大文字始まりの副題は保護されるべき）ため、回帰テストとして昇格させます。
*   **方針 (Design)**: `test_debug_header2.py` に `assert "The Ethnographic Effect I" in res2` などの明示的な検証処理を追加します。

---

## Conclusion & Next Steps
1. `core/pdf_ingester.py` の `remove_inline_running_headers` 関数に、空行をスキップする `non_empty_idx` カウンターを導入します。
2. `tests/test_debug_header.py` と `tests/test_debug_header2.py` のアサーションを整備・強化します。
3. 設計の本質を揺るがす構造的変更（正規表現の縮小）は行わず、現在の堅牢な `islower()` ガードを維持します。
