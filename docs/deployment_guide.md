# p2workflowy デプロイ手順書 (Streamlit Cloud 版)

この手順に従うことで、`p2workflowy` を自分専用の URL で公開し、他の人もブラウザから使えるようになります。

## 📋 事前準備
1.  **GitHub アカウント**: 自身のリポジトリ `amanefjt/p2workflowy` に最新のコードが反映されていること。
2.  **Streamlit Cloud アカウント**: [streamlit.io/cloud](https://streamlit.io/cloud) で GitHub アカウントを使ってサインアップしてください。

## 🚀 デプロイ手順

1.  **新しいアプリの作成**: 
    - Streamlit Cloud のダッシュボードで 「Create app」 ボタンを押します。
2.  **リポジトリの選択**: 
    - `amanefjt/p2workflowy` を選択します。
    - **Main file path**: `src/app.py` と入力してください。
3.  **高度な設定 (Optional)**:
    - デフォルトの設定で問題ありません。
4.  **デプロイ完了**: 
    - 「Deploy!」 を押すと、数分後に `https://xxx.streamlit.app` という URL が発行されます。

## 🔐 セキュリティについて
- 公開された URL は誰でもアクセス可能ですが、**各ユーザーが自分の API キーを入力しない限り、一切の処理（お金の発生）は行われません。**
- キーはセッション内でのみ保持され、サーバーには保存されません。

## 💡 トラブルシューティング
- もし起動時にエラーが出る場合は、Streamlit Cloud のコンソール画面（右下）を確認してください。依存関係のエラーの場合は、`requirements.txt` が正しく読み込まれているかチェックします。
