# [Knowledge Base] Phase間 I/O 仕様 (Data Contracts)

各 Phase は独立したモジュールとして動作し、以下のデータ構造を厳守すること。各 Phase の出力は state/ ディレクトリ内に個別のファイルとして保存（蓄積）され、後段の Phase がそれを参照する。

Phase 別 State ファイル定義
- phase1_clean.json: クレンジング済みの List[RawChunk]

- phase2_meta.json: resume_content (str) と keywords_data (list)

- phase3_structure.json: english_tree (List[TreeNode])

- phase4_translation.json: japanese_tree (List[TreeNode])

翻訳用 Glossary 形式の定義
keywords_data と glossary.csv をマージし、以下の形式の文字列としてプロンプトの {glossary_content} に注入すること。

形式: - [English Term]: [Japanese Translation] ([Definition])

優先順位: 同一の用語がある場合は、ユーザー提供の glossary.csv を優先し、LLM 抽出の結果を上書きする。

Phase 3 出力 (State 保存・2 種類)
3a. Phase 4 への入力 (翻訳用辞書)
Dict[str, List[Dict]] (セクション名でグループ化されたチャンク)

Key: セクション名 (例: "Introduction", "[Unlabeled Section]")

Value: チャンクのリスト [{"id": 1, "text": "..."}, ...]

3b. Phase 5 への入力 (英語ツリー)
List[TreeNode] (構造化済みのツリー)

構築ルール: セクション名を role: "h2"、内部チャンクを role: "p" とし、h2 の children に p を追加する。

Phase 4 出力 / Phase 5 入力 (日本語ツリー)
List[TreeNode] (Phase 3b の骨格を維持し、text のみが日本語に置き換わったツリー)

[実装手順 A：翻訳リクエストの並列化と組み立て]
並列処理の許可: セクション単位（"Abstract", "Introduction" 等）での Async による並列実行を許可する。セクションを跨ぐ文脈（Sliding Window）の維持は不要である。

セクション内処理: 同一セクション内のチャンク翻訳は、Sliding Window を適用するため直列に処理すること。

各セクションの翻訳結果 [{"id": ..., "ja": "..."}] を、id をキーとした検索用辞書にまとめる。

プロンプト変数: プロンプト内の `{expertise}`, `{context_guide}`, `{resume_content}`, `{glossary_content}` などのプレースホルダーはコード側で適切に置換すること。

Sliding Window 制約: previous_translation に渡すのは、**「直前 1 チャンク（最大 3 段落分）」**の翻訳結果のみとする。2 チャンク以上の蓄積はトークン爆発防止のため禁止する。

[実装手順 B：日本語ツリーの組み上げ]
Phase 3b の english_tree をディープコピーして japanese_tree のベースを作成する。

各セクションの翻訳結果 [{"id": ..., "ja": "..."}] を、id をキーとした検索用辞書にまとめる。

コピーしたツリーを再帰的に走査し、ノードの id が辞書に存在する場合、その text フィールドを翻訳後の ja テキストで上書きする。

翻訳に失敗した、または LLM が返さなかったノードについては、text を "[翻訳エラー] {元の英文}" と記述して構造を維持すること。

データクラス定義 (models.py)
Python
from dataclasses import dataclass, field
from typing import List, Union

@dataclass
class RawChunk:
    id: Union[str, int]
    text: str
    seq_index: float

@dataclass
class TreeNode:
    id: Union[str, int]
    text: str
    role: str          # "h2" または "p"
    seq_index: float   # 物理的な出現順序を保持
    children: List["TreeNode"] = field(default_factory=list)