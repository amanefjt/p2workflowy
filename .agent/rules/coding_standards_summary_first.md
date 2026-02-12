# Summary-First Approach コーディング規約

`p2workflowy` プロジェクトにおける、論文処理パイプラインの基本設計指針です。

## 1. 三段階の逐次パイプライン
新しい機能を追加したり、プロンプトを改修したりする場合は、必ず以下の三段階のフローを維持してください。

1.  **Phase 1: Semantic Mapping (要約生成)**
    - 原文から直接「論理構造の地図（要約）」を作成する。
    - この段階で、論文の主要な章立て、議論の展開を確定させる。
2.  **Phase 2: Anchored Structuring (構造化)**
    - Phase 1 の要約を「ヒント」として LLM に渡し、原文の構造化を行う。
    - **ルール**: 原文の見た目（フォントサイズ等）より、要約の論理構造を優先して見出しを付与する。
3.  **Phase 3: Contextual Translation (文脈を考慮した翻訳)**
    - 構造化された Markdown を翻訳する際、Phase 1 の要約をコンテキストとして全スレッド（チャンク）に共有する。

## 2. インターフェースの共通化
- **CLI版** (`src/main.py`) と **Web版** (`web/src/App.tsx`) の両方から同じコアロジックを呼び出せるようにする。
- Python版は `src/skills.py`, `src/utils.py` に実装。
- TypeScript版は `web/src/processor.ts`, `web/src/utils.ts` に実装。
- 両者のロジックは完全に一致させ、同じ入力に対して同じ出力を生成する。
- API キーやモデル名などの設定値は、コンストラクタ経由で動的に注入（Injection）できるように設計する。

## 3. 見出し階層の正規化
- 最終的な出力直前に `Utils.normalize_markdown_headings` を通し、文書全体の階層が `# (H1)` から始まるように強制補正する。
- Workflowy への貼り付けを考慮し、本文は直近の見出しよりも一段階深いインデントで出力する。

## 4. Web版の特性
- ブラウザ完結型（サーバー不要）
- BYOK (Bring Your Own Key) モデル: ユーザーが各自の Gemini API キーを入力
- 静的サイトとして Cloudflare Pages 等で公開可能
- UI は日本語、モデル選択は `gemini-3-flash-preview` (推奨) と `gemini-2.5-flash`
