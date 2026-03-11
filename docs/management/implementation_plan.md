# 実装計画：論理的バグの修正と品質向上 (p2workflowy V2)

指示に基づき、localStorageの挙動修正、サーバーのステータス表示のズレの解消、Markdown出力の階層適正化、およびサンプリングロジックの改善を実施します。

## 修正内容の解説

### 1. Markdownの見出しレベル不整合（例）
**現状（不具合）**:
```markdown
## English text
## Introduction  <-- Introductionが上位の "English text" と同じレベルになってしまう
...内容...
```
**修正後**:
```markdown
## English text
### Introduction <-- 正しくインデント（レベル3）され、English text の子要素になる
...内容...
```
`phase5_export.py` の `base_level` 渡しの数値を調整することで、文書全体の親子関係を正しく復元します。

### 2. サンプリングの「捨てすぎ」問題
**現状**:
30万文字（文庫本約1/2冊分相当）を超えた瞬間、**中間部分を24万文字分まるごとカット**し、冒頭4万字と末尾2万字だけをLLMに渡しています。
- **リスク**: 論文の核心である「論理の展開（中間部）」や「実験データ」が要約に一切含まれない。
- **改善案**: カットする文字数を緩和し（例：10万文字程度に抑える）、かつ中間部分からも一定間隔で抽出する「分散サンプリング」への切り替え、または制限値自体の引き上げを検討します。

## Proposed Changes

### 1. Web Frontend: localStorage & Logic Fixes
- **[MODIFY] [app.js](file:///Users/shufujita/Antigravity/p2workflowy/web/app.js)** / **[app_ronbun.js](file:///Users/shufujita/Antigravity/p2workflowy/web/app_ronbun.js)**
    - `localStorage.setItem` のガード条件を削除し、空文字でも保存（上書き）可能にする。
    - `expertise` の `|| '文化人類学'` を削除し、ユーザーの意図した「空入力」や「カスタム入力」を尊重する。

### 2. Server: Progress Status Alignment
- **[MODIFY] [server.py](file:///Users/shufujita/Antigravity/p2workflowy/server.py)**
    - 進捗の判定ロジックを「ファイルが存在するか」ベースから、実行中のフラグや各フェーズの完了信号ベースに変更（または判定の閾値を「次のフェーズ」へ1つずらす）。

### 3. Core: Export & Sampling Fixes
- **[MODIFY] [phase5_export.py](file:///Users/shufujita/Antigravity/p2workflowy/core/phase5_export.py)**
    - `tree_to_markdown` の `base_level` 引数を `3` に変更し、親階層より深く出力されるように修正。
- **[MODIFY] [phase2_meta.py](file:///Users/shufujita/Antigravity/p2workflowy/core/phase2_meta.py)**
    - `MAX_INPUT_CHARS` を引き上げ（Gemini 1.5 Pro/Flashの長いコンテキストに対応）、捨てられる情報を最小化する。

## Verification Plan
1. `app.js` の修正後、ブラウザの開発者ツールで `localStorage` が空文字で上書きされることを確認。
2. 特大サイズのテキスト（30万文字以上）を投入し、`phase2_meta.py` のログでサンプリング範囲が改善されているか確認。
3. 生成された `.md` ファイルをプレビューし、見出しの階層が正しいか確認。
