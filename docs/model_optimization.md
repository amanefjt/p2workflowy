# Gemini Model Optimization Guide

p2workflowy V2 における、各 Gemini モデルの最適化設定と運用ノウハウのまとめ。

## 1. 推奨モデル構成

| 用途 | 推奨モデル | 理由 | 備考 |
| :--- | :--- | :--- | :--- |
| **Web UI (既定)** | `gemini-3.1-flash-lite-preview` | **500 RPD** (Free tier) / **高速** (~30%減) | `Thinking: High` 必須 |
| **CLI / 高品質** | `gemini-3-flash-preview` | 応答が極めて安定。1チャンクあたりの推論が重厚。 | **20 RPD** 制限に注意 |
| **Legacy / 予備** | `gemini-2.0-flash` / `2.5-flash` | 3.1 Flash Lite 以前の主力。現在は 3.1 推奨。 | `ronbun` プロンプト必須 |

## 2. Thinking Level (思考レベル) の活用

Gemini 3.1 シリーズから導入された `thinking_level` パラメータを制御することで、Lite モデルでも高品質な翻訳・解析が可能です。

- **`HIGH` (高)**: 論理的なステップを細かく実行する。学術論文のレジュメ生成や複雑な構造の翻訳に必須。
- **自動設定**: `llm_client.py` では、モデル名に `gemini-3.1-flash` が含まれる場合に自動で `thinking_level: HIGH` をセットするように実装されています。

### SDK 実装上の注意 (Python)
`google-genai` SDK では、以下のように `thinking_config` のネスト内に配置する必要があります。
```python
config = types.GenerateContentConfig(
    thinking_config = types.ThinkingConfig(thinking_level="HIGH")
)
```

## 3. プロンプト戦略

モデルの特性（特に構造解析の安定度）に応じて、プロンプトを自動的に出し分けています。

- **標準プロンプト (`SUMMARY_PROMPT`)**:
  - 対象: `gemini-3.x` 系。
  - 特徴: 思考レベル向上により、通常の Markdown 指示だけで綺麗な見出し構造を抽出可能。
- **構造重視プロンプト (`SUMMARY_PROMPT_ronbun`)**:
  - 対象: `gemini-2.x` 系。
  - 特徴: 見出しの ` # [Original English Heading]` 形式を「死守」させるための、より厳しい指示文。

## 4. レート制限 (2026年3月現在)

Google AI Studio の無料枠における RPD (Requests Per Day) の現状：

- **Gemini 1.5 Flash / Pro**: サービス終了または大幅な制限。
- **Gemini 2.0 / 3.0 Flash**: **20 RPD**。
- **Gemini 3.1 Flash Lite**: **500 RPD**。

## 5. 性能比較データ (2026-03-11)

`Arbitrarysample.txt` (約 6.2万文字) でのパフォーンマンス比較：

| モデル | Thinking | 処理時間 | 品質 |
| :--- | :--- | :--- | :--- |
| `gemini-3-flash-preview` | **High** | 152s (約 2m 30s) | 安定・重厚な翻訳 |
| `gemini-3.1-flash-lite-preview` | **High** | **108s (約 1m 50s)** | 高速・構造化に強い |

> [!TIP]
> Gemini 3.1 Flash Lite は 152s → 108s と、約 **30% の高速化** を実現しています。500 RPD の恩恵と合わせ、実運用におけるメインモデルとして最適です。

> [!IMPORTANT]
> 大規模な論文（100+ チャンク）を翻訳する場合、20 RPD のモデルでは制限に達します。そのため、Web版では 3.1 Flash Lite をメインに据えるのが最適解です。以前検討された 2.5-flash を用いた「制限回避策」は、3.1 の登場により役割を終えました。
