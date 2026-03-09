# [Knowledge Base] セクションの構造化とファジィ照合 (Section Detector)
Phase 3 (Structuring) において、レジュメの見出しと本文チャンクを紐付け、セクション辞書およびツリー構造（english_tree）に分割するための仕様である。

## 1. Pre-scanner 結果の適用 (Hard Anchors)
Phase 3 の開始時、indi_pre_scanner.md から得られた以下のアンカー情報を、後続のファジィマッチングの結果よりも優先して適用すること。

Abstract の範囲確定: abstract_start_id から introduction_start_id の直前までの全チャンクを Abstract セクションとして固定する。

Introduction 以降の限定: ファジィマッチング（見出しの紐付け）は、原則として introduction_start_id 以降のチャンクに対してのみ実施する。

メタデータの除外: metadata_ids に含まれるチャンクは翻訳対象から除外し、role: "meta" として扱う。

## 2. レジュメからの見出し抽出（前処理）
Phase 2 で生成された resume_content から、ファジィマッチング用の英語見出しリストを以下のロジックで抽出すること。

抽出ルール: 先頭が # 1つ、かつ英大文字始まりの行のみを抽出する。

正規表現: r"^#\s+([A-Z][^\n]+)$"

除外ルール: ## や ### で始まる日本語のサブ見出し（「中心的な主張」「論理展開」等）は、マッチングのノイズとなるため絶対に抽出対象に含めないこと。

## 3. 見出しのマッチングアルゴリズム（Fuzzy Matching）
PDF 抽出テキスト特有の OCR ノイズや、見出し番号の有無（例: "1. Introduction" と "Introduction"）などの表記揺れを安全に吸収するため、単純な文字列比較ではなくファジィ検索を使用する。

[実装指定] 文字列の比較には thefuzz ライブラリの thefuzz.fuzz.token_set_ratio を必ず使用すること。

ratio や partial_ratio は語順やノイズに弱いため使用不可。

## 4. 判定閾値（Threshold）とフォールバック（Fallback）
閾値: token_set_ratio のスコアが 80 以上 の場合のみ、新しいセクションの開始と判定してチャンクを分割する。

フォールバック処理: スコアが 80 を満たす見出しが見つからないチャンク群（紐付けに失敗した段落）が発生した場合は、仮想的なセクション [Unlabeled Section] を作成し、そこに格納して処理を継続（救済）すること。

## 5. 除外セクションのクリッピング
判定ロジック（重要）: coreprompts.json の EXCLUDE_SECTION_KEYWORDS （例: "references", "conflict of interest"）との照合は、表記揺れに対応するため以下のいずれかで行うこと。

部分一致: .lower() で正規化した見出し文字列の中に、キーワードが含まれているか判定する。

高精度ファジィ: token_set_ratio でスコアが 90 以上 であるか判定する。

処理: マッチしたセクション（例: "Declaration of Competing Interest"）以降のチャンクは配列からスライスして完全に破棄し、後段の翻訳処理には送らないこと。