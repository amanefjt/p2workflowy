# APIキー取得ガイド UX 設計

**日付**: 2026-05-11  
**対象**: `web/index.html`, `web/app.js`, `web/style.css`

---

## 背景と目的

Web版 p2workflowy のターゲットユーザーは、PCとGmailは持っているがAPIキーの概念を知らない層（例：文学部の学部生）。現在の「APIキーについて」説明文は技術的文脈が前提になっており、初見では「お金がかかるかもしれない」という不安を与えている。

**目標**: APIキーの取得手順と料金を、メインUIの使い心地を損なわずに非技術者に伝える。

---

## 前提：Web版のモデル固定

Web版は `gemini-3.1-flash-lite-preview` のみを使用する。

- カード登録不要、無料で使用可能（1日500リクエストまで）
- 有料プランに移行しても Flash Lite は料金ゼロ
- 「実質無料」と明確に言い切れる根拠となる
- CLI版は引き続き TierManager による `gemini-3-flash-preview` → `gemini-3.1-flash-lite-preview` 自動切り替えを維持

---

## 設計

### APIキーセクションの変更

**削除するもの**:
- 現在の `api-note` div（長い説明文2段落）

**追加するもの**:

```
[✅ 無料で使えます（カード登録不要）]  ← 緑バッジ

[APIキーを入力してください...]  [🔑 取得方法を見る]  ← 入力欄＋ボタン横並び

入力したAPIキーはブラウザにのみ保存されます。  ← 小テキスト
```

バッジは `background: #dcfce7; border: 1px solid #86efac; border-radius: 20px` の pill 形状。ボタンは既存の accent 色（`#6366f1`）を使用。

---

### モーダル

「取得方法を見る」ボタンクリックで表示。ESCキーまたは「✕」で閉じる。

#### ヘッダー
- 背景: `#6366f1`（accent 色）
- タイトル: 「🔑 Gemini APIキーについて」
- 右端に閉じるボタン（✕）

#### 本文（上から順）

**① 無料バッジ（大）**
```
✅ 無料で使えます
カード登録は不要です。Gmailアカウントだけで始められます。
```
背景: `#dcfce7`

**② 取得手順（5ステップ）**
1. [Google AI Studio](https://aistudio.google.com/app/apikey) をGmailで開く
2. 「APIキーを作成」ボタンをクリック
3. 「新しいプロジェクトでAPIキーを作成」を選択
4. 表示されたキー（`AIza...`）をコピー
5. このページの入力欄に貼り付け

**③ 料金カード（2枚）**

🟢 **無料プラン（カード不要）**
> 1日500リクエストまで無料。論文数本分の処理に相当します。

🟡 **有料プラン（より多く使いたい場合）**
> 従量課金制。論文1本あたり数円〜10円程度の目安です。

**④ プライバシー注記**
> ⚠️ 無料プランではGoogleの規約上、入力内容がモデル改善に使用される可能性があります。機密性の高い文書を扱う場合は有料プランをご検討ください。

---

## 実装スコープ

### `web/index.html`
- `api-note` div を削除
- 緑バッジ追加
- 入力欄とボタンを横並びに変更
- モーダル HTML を `</body>` 直前に追加（初期状態 `display:none`）

### `web/style.css`
- `.api-badge`: 緑pill バッジ
- `.api-key-row`: 入力欄＋ボタン横並び
- `.modal-overlay`: 全画面半透明オーバーレイ
- `.modal-box`: モーダル本体（max-width: 520px、border-radius: 12px）
- `.modal-header`: accent 背景
- `.plan-card`: 料金カード（green/yellow 2種）

### `web/app.js`
- `openApiModal()` / `closeApiModal()` 関数
- ESCキーでの閉じる処理
- オーバーレイクリックでの閉じる処理

### `core/llm_client.py` または `server.py`（確認事項）
- Web経由のリクエストが `gemini-3.1-flash-lite-preview` を使うよう、モデル選択ロジックを確認・修正
- 有料APIキーをWeb版に入力した場合も Flash Lite を使う（意図的な仕様）。ユーザーが高品質処理を望む場合はCLIを使うよう誘導するため、この制約はドキュメントまたはUI上に明示しない

---

## 非スコープ

- CLI版の動作変更（TierManagerはそのまま）
- Liteモードの品質改善
- 管理者モード（`APP_ADMIN_PASSCODE`）の変更
