# Phase 5 エクスポートロジック微調整と Cloudflare 環境同期：設計設計

## 1. 概要 (Overview)
Phase 5 の出力構造（Markdown / Workflowy）をユーザーの提示した理想的な構造（`ideal_mdstructure.md`, `ideal_wfstructure.txt`）に合わせ、階層の適正化を行いました。
また、Cloudflare Pages の配信設定（`web` をルートとして配信）に伴い、ローカルサーバーとの環境同期を実施しました。

## 2. 設計方針と採用の根拠 (Rationale)

### 2.1 エクスポートモードの分離と見出しレベルの調整
- **分離理由**: `p2workflowy` (レジュメ+英語+日本語) と `ronbunnihongo` (日本語のみ) の出力形式を分離し、各モードの構造（H2 / H3 レベル、ラッパーの有無）を個別に定義できるようにしました。
- **見出しレベルの採用**:
  - **英語 (H3)**: インポート後にセクションとして扱いやすくするため。
  - **日本語 (H2)**: 日本語がメインの出力として機能するため。

### 2.2 「日本語本文」セパレーターの導入
- **課題**: Workflowy にインポートした際、日本語セクションが直前の英語ノード（English text 等）の下に誤ってネストされる。
- **解決策**: Markdown において `## 日本語本文` (H2)、Workflowy において `- 日本語本文` (Level 1) のセパレーターを導入しました。これにより、外部アプリが各セクションの境界を正しく認識し、不適切なネストを防ぎます。

### 2.3 Workflowy 親ノードのインデント適正化
- **設計**: `tree_to_workflowy` 関数を呼び出す際、日本語ツリーに対しては `base_depth=0` を設定。
- **採用根拠**: 日本語の各セクション（Abstract, Conclusion 等）を Level 1 ノード（インデントなし）にすることで、インポート作業時に「 English text / 日本語本文 」と並列なレベルでセクション化され、使い勝手が向上します。

### 2.4 Cloudflare 配信資産パスの同期 (Cloudflare Sync)
- **資産パスの変更**: `/web/style.css` -> `/style.css` への変更。
- **理由**: Cloudflare Pages で `web` ディレクトリをビルド出力として設定すると、その中のファイルはルート直下として配信されます。ローカルと本番でパスを一致させるため、HTML タグと `server.py` のマウント設定を同期しました。
- **FastAPI ルーティング**: `app.mount("/", ...)` をコードの最後に記述。これにより、静的ファイルマウントが `/ronbun` や `/api/process` などの特定のパス定義を上書き（シャドウイング）することを防ぎます。

## 3. 理想の構造リファレンス
- [ideal_mdstructure.md](file:///Users/shufujita/Antigravity/p2workflowy/docs/export_spec/ideal_mdstructure.md)
- [ideal_wfstructure.txt](file:///Users/shufujita/Antigravity/p2workflowy/docs/export_spec/ideal_wfstructure.txt)
- [ideal_ronbunmdstructure.md](file:///Users/shufujita/Antigravity/p2workflowy/docs/export_spec/ideal_ronbunmdstructure.md)
