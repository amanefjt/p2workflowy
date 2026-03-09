# Task: RonbunNihongo 専用インターフェースの実装

学術論文を日本語のMarkdownとして読むことに特化した、独立したUIと書き出し機能を提供します。

## ステータス
- [x] 準備・設計
    - [x] 既存パイプラインの流用調査
    - [x] 「日本語のみ」出力ロジックの要件定義
- [x] 開発 (Development)
    - [x] Core: RonbunNihongo専用のMarkdown出力関数の追加 (`phase5_export.py`)
    - [x] Web: 専用HTML (`ronbun.html`) の作成
    - [x] Web: 専用JavaScript (`app_ronbun.js`) の作成
    - [x] Web: 全メニュー・注釈の日本語化
    - [x] Server: `/ronbun` へのルーティング追加
- [x] ブラッシュアップ (Optimization)
    - [x] Web版進捗表示の簡略化（詳細ログ非表示、ざっくりパーセント）
    - [x] レジュメ（要約）の出力からの除外（ユーザー要望）
    - [x] 既存 `index.html` への影響除去（完全UI分離）
- [x] 検証・デプロイ
    - [x] `/ronbun` からの正常変換・ダウンロード確認
    - [x] GitHubへの反映
