# Model Optimization Guide: Gemini 3 Flash

Gemini 3 Flash は、最先端の知能を圧倒的なスピードと低コストで提供するモデルです。公式ドキュメントおよび最新の発表内容に基づき、当プロジェクトにおける最適化設定を定義します。

## Model Specifications
- **Model Name**: `gemini-3-flash-preview`
- **Max Input Tokens**: **1,048,576** (1M)
- **Max Output Tokens**: **65,536** (65k)
- **Thinking Mode**: 対応。複雑なユースケースでは性能が向上。
- **Performance**:
    - **GPQA Diamond**: 90.4% (博士レベルの推論能力)
    - **SWE-bench Verified**: 78% (Gemini 3 Pro を上回るコーディング性能)
    - **Speed**: Gemini 2.5 Pro の **3倍の処理速度** を実現。
    - **Cost**: 入力 $0.50 / 1M tokens, 出力 $3.00 / 1M tokens (極めて低コスト)。

### Optimization Strategy
Gemini 3 Flash の広大な出力トークン制限 (65k) を活かしつつ、日本語翻訳時のテキスト膨張と AI による要約化（Summarization）を防ぐため、フェーズごとに最適なチャンクサイズを設定します。

### Recommended Settings
- **MAX_STRUCTURING_CHUNK_SIZE**: **15,000 characters**
    - 構造化（Phase 2: 英→英）において、AI の「出力スタミナ（書き切り限界）」に配慮した設定。
    - 40,000文字では大規模な論文で AI が勝手に要約を始めてしまうリスクがあるため、翻訳サイズと同等の 15,000文字に抑えるのが最も安全で高精度です。
- **MAX_TRANSLATION_CHUNK_SIZE**: **15,000 characters**
    - 翻訳（Phase 3: 英→日）では、翻訳後の日本語が安全に出力上限（および AI の「書き切り」の限界）に収まるように設定。
    - 40,000文字の翻訳では、AI がトークン消費を抑えるために意図せず要約を行うリスクがあるため、15,000文字程度に抑えるのが最も安定します。

## Configuration
`shared/prompts.json` で以下の通り定義されています：
- `MAX_STRUCTURING_CHUNK_SIZE`: `40000`
- `MAX_TRANSLATION_CHUNK_SIZE`: `15000`
