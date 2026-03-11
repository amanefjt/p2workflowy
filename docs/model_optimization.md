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

## 6. モデル選定と運用に関する知見 (V3 安定版まとめ)

### 6.1. UIとロジックのシンプルな統合
Web版（`index.html`, `ronbun.html`）におけるモデル選定およびThinking Levelのオプションは、**あえてユーザーに選択させない**方針を中心に据えました。
- ユーザーに「品質と速度のトレードオフ」を問うことは、論文を素早く読みたいという本来の目的にノイズになります。
- 裏側で一律 `gemini-3.1-flash-lite-preview` と `Thinking: High` をバインドすることで、最良の結果を「意識せずとも」得られるようにしました。

### 6.2. Thinking Level "High" の絶大な効果
これまでの `gemini-2.0-flash` や `2.5-flash` では、Markdownの見出し構造（特に `# [Original Heading]` 形式）を維持させるために、極度に制約の強いプロンプト（`SUMMARY_PROMPT_ronbun`など）を必要としていました。
しかし、`Thinking: High` を適用した `gemini-3.1-flash-lite` では：
1. **文脈の理解度が劇的に向上**: プロンプトの細かいハック（例：「必ずこう返せ」という強い禁止事項）に頼らなくとも、セクション構成や用語集（Glossary）の適用を忠実に守ります。
2. **"Unlabeled Section" の低減**: 以前頻発していた、構造がパースできずに「ラベルなしセクション」として一塊になってしまう現象がほぼ解消されました。
3. **処理スピードの底上げ**: 複雑な推論（Highレベル）を行わせても、1万文字前後で約1分50秒という、前世代と遜色ない（またはそれ以上の）圧倒的スピードで返答します。

### 6.3. API制限（Rate Limit）問題の最終解決
最も大きな収穫は、無料枠のクォータ制限（RPD: Requests Per Day）問題が解消されたことです。
- 従来の `gemini-3-flash` (20 RPD) では、長文論文をチャンク化して投げるアプローチとは致命的に相性が悪く、数回の翻訳テストで制限に達していました。
- `gemini-3.1-flash-lite-preview` (500 RPD) の採用により、実質的に「無料枠の限界を気にせず、思う存分に翻訳できる」状態が実現しました。これは、Web版を誰にでも使ってもらえるツールとして解放するための、最も決定的なピースでした。
