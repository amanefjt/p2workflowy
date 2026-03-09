# Export Format Specifications

このディレクトリには、プロジェクトの各エクスポートモードにおける「理想的な出力構造」を示すリファレンスファイルが格納されています。
開発や検証の際に、生成されたファイルがこれらの構造に準拠しているかを確認するために使用します。

## ファイル構成

- [ideal_mdstructure.md](file:///Users/shufujita/Antigravity/p2workflowy/docs/export_spec/ideal_mdstructure.md)
  - `p2workflowy` モード（デフォルト）の Markdown 出力用の理想的な階層構造です。
  - 英語原文セクションは `###` (H3)、日本語訳セクションは `##` (H2) を使用します。
- [ideal_wfstructure.txt](file:///Users/shufujita/Antigravity/p2workflowy/docs/export_spec/ideal_wfstructure.txt)
  - `p2workflowy` モードの Workflowy 用テキスト出力の理想的なインデント構造です。
  - 余計な親ノード（「日本語テキスト」など）を排した形式になっています。
- [ideal_ronbunmdstructure.md](file:///Users/shufujita/Antigravity/p2workflowy/docs/export_spec/ideal_ronbunmdstructure.md)
  - `ronbunnihongo` モードの Markdown 出力用の理想的な構造です。
  - 日本語訳のみを抽出し、タイトルと H2 見出しで構成されます。

## 使用方法
UI や CLI プログラムの修正を行った際、`data/sample/nosuchthings/NSTsample.txt` 等のテストデータを用いて生成された出力ファイルと、これらのリファレンスファイルを比較して品質を担保してください。
