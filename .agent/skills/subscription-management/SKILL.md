---
name: subscription-management
description: Use when managing or implementing SaaS-related logic including Clerk authentication, Stripe billing, and user quota enforcement in P2Workflowy.
---

# Subscription Management Skill (SaaS 運用スキル)

## 概要 (Overview)
P2Workflowy を SaaS として維持・運用するための、認証、決済、クォータ管理に関する手順を定義する。
`server.py` の拡張、`quota_service.py` の連携、およびフロントエンドの Clerk 統合を対象とする。

## 手順 (Steps)

### 1. ユーザー識別の確認 (Auth Check)
- HTTP リクエストヘッダーから Clerk JWT を抽出する。
- `python-jose` を使用して署名を検証し、`clerk_id` を取得する。
- 未ログイン時は、強制的に BYOK (User API Key) モードへ移行する。

### 2. クォータ判定とフォールバック (Quota Decision)
- リクエスト開始前に `quota_service.get_user_quota(clerk_id)` を呼び出す。
- `tokens_used < quota_limit` かを確認。
- **超過時**: `tier_manager.set_tier(GeminiTier.FREE)` を実行し、UI 通知フラグを立てる。ユーザーの持込キーを取得する。

### 3. トークン消費の記録 (Token Tracking)
- LLM 呼び出し終了後、`usage_metadata` からトークン数を抽出する。
- `quota_service.update_usage(clerk_id, tokens)` をアトミックに実行する。
- 消費ログは、後日監査およびダッシュボード表示に使用するため正確に記録する。

### 4. 決済 Webhook 処理 (Billing Integration)
- Stripe からの `checkout.session.completed` を受信した際、DB の `plan_type` を `pro` に更新する。
- 同時に、初期クォータ（2M tokens 等）をセットする。

## 注意事項 (Precautions)
- **ステートレス性**: バックエンドはステートレスを維持し、クォータ情報は常に Supabase/D1 から取得すること。
- **エラー処理**: DB 接続エラー時は、安全のため BYOK モードにフォールバックさせ、システム停止を防ぐこと。
- **Model Optimization 遵守**: `パフォーマンス標準.md` に従い、ティアに応じた適切なモデル（1.5 Pro vs Flash）を割り当てること。
