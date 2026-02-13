# Model Optimization Guide: Gemini 3 Flash

Based on the [official documentation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-flash?hl=ja) (Preview), here are the specifications and optimization strategies for `gemini-3-flash`.

## Model Specifications
- **Model Name**: `gemini-3-flash-preview`
- **Max Input Tokens**: **1,048,576** (1M)
- **Max Output Tokens**: **65,536** (65k)

## Chunk Size Optimization
For tasks like **Full Text Structuring** and **Translation**, the limiting factor is the **Output Token Limit**.
Since we require the model to output the full content (rewriting/translating 1:1), the Input size must be smaller than the Max Output Tokens to avoid truncation.

### Calculation
- **Max Output**: 65,536 tokens.
- **Conversion**: Roughly 1 token ≈ 4 English characters (conservative) or 2-3 Japanese characters.
- **Safe Output Character Limit**: 
    - 65,536 tokens * ~2.5 chars/token ≈ 163,840 characters.
- **Safety Margin**: To prevent the model from "rushing" or summarizing due to length pressure, and to allow for markup overhead, we set a safe input chunk size.

### Recommended Setting
- **Old Setting**: 12,000 characters (Very conservative, tailored for 8k/16k output limits).
- **New Optimized Setting**: **40,000 characters**.
    - 40,000 chars ≈ 10,000 - 15,000 tokens.
    - This corresponds to ~23% of the max output capacity (65k).
    - This provides a massive safety buffer while significantly reducing the number of API calls and improving context continuity compared to smaller chunks.

## Configuration
Set `MAX_TRANSLATION_CHUNK_SIZE` in `shared/prompts.json` to `40000`.
