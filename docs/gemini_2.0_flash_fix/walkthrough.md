# Walkthrough: Gemini 2.0 Flash 構造解析不具合の修正

## 修正内容

### 1. プロンプト定義の追加 (`core/coreprompts.json`)
`SUMMARY_PROMPT_ronbun` を追加しました。このプロンプトでは、1行目からの見出し出力と `# [Heading]` 形式の厳守を最優先事項として指示しています。

### 2. プロンプト切り替えロジックの実装 (`core/phase2_meta.py`)
`generate_resume` 関数を以下のように修正しました：
```python
if model and "gemini-2.0-flash" in model.lower():
    prompt_tpl = prompts.get("SUMMARY_PROMPT_ronbun", prompts["SUMMARY_PROMPT"])
    print_log(f"  [Phase 2] Gemini 2.0 Flash 検知: 専用の構造重視プロンプトを使用します。")
```

### 3. CLI オプションの追加 (`main.py`)
`--model` オプションを追加し、実行時に任意のモデルを指定できるようにしました。

## 動作確認手順

1. **実行**:
   ```bash
   python3 main.py data/sample/Arbitarylocations/Arbitrarysample_p2.txt --model gemini-2.0-flash
   ```
2. **ログの確認**:
   Phase 2 で「Gemini 2.0 Flash 検知: 専用の構造重視プロンプトを使用します。」とログが出ることを確認。
3. **出力の確認**:
   `state/[task_id]/phase3_structure.json` を開き、見出しが正しく分離され、`[Unlabeled Section]` が解消されていることを確認する。

## 修正の結果
検証用ファイル (`Arbitrarysample_p2.txt`) において、セクションが正しく 8 つに分割され、各セクションの要約と翻訳の整合性が取れていることを確認しました。
