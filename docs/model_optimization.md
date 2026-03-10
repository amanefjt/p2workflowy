# Model Optimization Guide: Gemini 3 Flash

Gemini 3 Flash は、最先端の知能を圧倒的なスピードと低コストで提供するモデルです。

> **公式ドキュメント**: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-flash?hl=ja

## Model Specifications

### Gemini 3 Flash (Performance Leader)
- **Model Name**: `gemini-3-flash-preview`
- **Max Input Tokens**: 1,048,576
- **Max Output Tokens**: 65,536
- **Strength**: 圧倒的な推論能力と速度。CLI版のデフォルト。

### Gemini 2.5 Flash (Next Gen)
- **Model Name**: `gemini-2.5-flash`
- **Max Input Tokens**: 1,048,576
- **Max Output Tokens**: 8,192
- **Strength**: 高い知能を持つが、無料枠が極めてタイト。

### Gemini 2.0 Flash (Utility King)
- **Model Name**: `gemini-2.0-flash`
- **Max Input Tokens**: 1,048,576
- **Max Output Tokens**: 8,192
- **Strength**: 無料枠が広く、プロトタイプや長時間ジョブに最適。

## API Rate Limits (Google AI Studio / 2025.12 Update)

2025年12月の仕様改定により、最新モデルの無料枠が大幅に制限されました。

| モデル名 | 1日の制限 (RPD) | 1分の制限 (RPM) | 備考 |
| :--- | :--- | :--- | :--- |
| **Gemini 2.5 Flash** | **20** | 10 | 1つのタスクで20回以上呼ぶとエラー。 |
| **Gemini 2.0 Flash** | **1,500** | 15 | パイプライン処理を完走可能。 |
| **Gemini 1.5 Flash** | **1,500** | 15 | 安定した従来モデル。 |

> [!IMPORTANT]
> Web版のパイプライン（レジュメ生成＋分割翻訳）では、通常合計20〜50回のリクエストが発生します。そのため、**20 RPD制限のある Gemini 2.5 Flash では途中で確実にエラー（429 RESOURCE_EXHAUSTED）が発生します。**

## Optimization Strategy
Gemini 3 Flash の出力トークン制限 (65k tokens) は、理論上は**日本語テキストで約20,000〜30,000文字**程度まで一気に出力可能です。

### Practical Limits (Battle-Tested)
実戦においては、以下の理由から理論値よりも低いしきい値を採用しています：
- **タスクの重さ**: 「一字一句漏らさぬ転写 ＋ OCRノイズ除去 ＋ 構造化」という複合タスクでは、AI の内部的な推論負荷（トークン消費）が高まり、理論限界に達する前にスタミナ切れ（出力停止）を起こす傾向がある。
- **レート制限への対応**: Web版では無料枠の広い `gemini-2.0-flash` を採用することで、長文の分割翻訳でも制限にかからずに完走させる戦略を採っている。


