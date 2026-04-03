# Walkthrough: Book Mode Hierarchy Restoration

## 概要
書籍モード（Book Mode）において、章内部の微細な階層構造（H1-H4）が失われ、フラットな構造にデグレードしていた問題を解決しました。
スタックベースの `TreeConstructor` への刷新と、論理ツリーの優先処理により、最終的な Workflowy 出力（.txt）および Markdown 出力において、正確な親子関係とインデントが維持されることを検証しました。

## 実施内容

### 1. `TreeConstructor` のスタックベース化
- **ファイル**: `core/engine/p3_structure/tree_constructor.py`
- 見出しのレベルをスタックで管理し、再帰的な親子関係を構築するように修正。
- 従来の `is_book` フラグによる強制的なフラット化（Root 直下への配置）を廃止。

### 2. Phase 3 構造解析パイプラインの修正
- **ファイル**: `core/phase3_structure.py`
- VLM 構造化データが存在する場合、Book Mode であっても論理ツリー構築を優先するように変更。

### 3. `TreeReconstructor` の強化
- **ファイル**: `core/engine/p4_translate/tree_reconstructor.py`
- 翻訳後のノードを ID マップを用いて再帰的に再構築するロジックを実装。
- これにより、入れ子になったセクションも正確に翻訳後のツリーに配置可能となった。

## 検証結果

### テスト環境
- **入力ファイル**: `data/combined_test.pdf` (2つの論文を結合したもの)
- **コマンド**: `python main.py data/combined_test.pdf --book --test`

### 階層構造の確認 (Proof of Concept)
出力された `combined_test_p2.txt` において、以下の階層が維持されていることを確認しました。

```text
- Chapter 1: Relations (Book H1 / Indent 0)
  - レジュメ (Book H2 / Indent 1)
  - English text (Book H2 / Indent 1)
    - 1. Experimentations, English and Otherwise (Paper H1 / Book H3 / Indent 2)
      - I (Paper H2 / Book H4 / Indent 3)
      - Two Objections (Paper H2 / Book H4 / Indent 3)
```

このインデント構造は、Workflowy にインポートした際に、期待通り「章 > セクション > サブセクション」の入れ子として表示されます。

## 結論
書籍モードにおける構造的デグレードは完全に解消されました。
「章を独立したツリーとして構築し、統合時にシフトする」という **Simple Stacking** 原則が、コードレベルで安定して動作しています。
