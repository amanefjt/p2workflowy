# 大規模コードのモジュール化プロトコル (modularization-protocol)

## 概要
1,000 行を超えるような巨大なモノリス・モジュールを、保守性の高い「アトミック・エンジン」へと安全に分解するための技術手順。

## 1. 責務の抽出 (Responsibility Audit)
- 機能を **「知能（Heuristics/Logic）」** と **「管理（Orchestration）」** に分ける。
- `if/else` による分岐が多発している箇所を特定し、戦略パターン（Strategy Pattern）への移行準備を行う。

## 2. 300行ルール (The 300-Line Rule)
- 1つの Python ファイル/クラスは、例外なく **300 行以内** に収める。
- これを超える場合、以下のサブモジュール（例: Phase 3 の場合）への分割を強制する：
    - `*_detector.py`: 物理/論理的な判定ロジック
    - `*_extractor.py`: 特定データの抽出・パース
    - `*_constructor.py`: データの再構成・ツリー構築

## 3. インターフェースの固定 (Data Model First)
- 分割後のモジュール間通信は、必ず `core/models.py` で定義された `RawChunk`, `TreeNode` 等の不変なデータモデルを介して行う。
- 引数に巨大な辞書（Dict）を渡すことを避け、必要なプロパティのみを抽出した `Context` クラス等を活用する。

## 4. 段階的リファクタリング手順 (Step-by-Step)
1. **検証環境の固定**: `scripts/verify_golden_rewrite.py` のような統合テストがパスする状態を維持する。
2. **ユーティリティの独立**: `text_utils.py` 等への共通処理の移動。
3. **エンジンの切り出し**: ロジックの本体を `core/engine/` 配下の新ファイルへ移動。
4. **オーケストレーターの修正**: 元のフェーズ・モジュール（例: `phase3_structure.py`）を、新エンジンを呼び出すだけの薄いラッパーに変更。
5. **不使用コードの除去**: 旧バージョンのロジックを `core/logic/legacy/` へ退避。

## 5. 品質検証
- 分割前後で、`verify_golden_rewrite.py` の出力（JSON/Markdown）に差分がないことを確認する。
- `ruff` や `mypy` による静的解析を行い、インポートの循環参照が発生していないかチェックする。
