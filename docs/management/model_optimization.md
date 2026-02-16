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

## Chunk Size Optimization
構造化（Phase 2）および翻訳（Phase 3）において、制限要因となるのは **出力トークン制限 (65k)** です。
原文を 1:1 で出力・翻訳する場合、入力サイズは出力制限内に収まる必要があります。

### Calculation
- **Max Output**: 65,536 tokens.
- **Character Conversion**: 1 token ≈ 約3〜4文字 (英日混在環境)。
- **Capacity**: 最大約 200,000文字程度の出力が可能。
- **Optimization Strategy**: Gemini 3 Flash の高速な処理能力を活かし、チャンクサイズを大きく設定することで、セクションの分断を防ぎ、文脈（Context）の維持を優先します。

### Recommended Setting
- **Optimized Setting**: **40,000 characters**.
    - 40,000文字は約 10,000〜15,000トークンに相当。
    - 出力制限 (65k) の約 20〜23% に抑えることで、ハルシネーションや不完全な出力を防ぐ十分なセーフティバッファを確保。
    - 並列翻訳パイプライン（Phase 3）においても、リクエスト回数を減らしつつ高速な処理を実現。

## Configuration
`shared/prompts.json` の `MAX_TRANSLATION_CHUNK_SIZE` を `40000` に設定します。
