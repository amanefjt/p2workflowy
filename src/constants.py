# -*- coding: utf-8 -*-
"""
定数とプロンプトの定義
"""

# Gemini モデル設定
DEFAULT_MODEL = "gemini-3-flash-preview"

# --- Skill: StructureRestorer (構造復元) ---
STRUCTURING_PROMPT = """あなたは学術論文の編集AIです。
入力されたテキストはPDFから抽出されたもので、改行が崩れ、ノイズが含まれています。
意味論的に正しいMarkdown構造を復元してください。

<rules>
- 文中の不自然な改行（Hyphenation）を連結し、文を繋ぎ直す。
- ページ番号、ジャーナル名、DOI、ヘッダー、フッターなどのノイズ行を削除する。
- **除外ルール**: 以下の項目およびそれに類するセクションは、内容を含め一切出力しないでください（構造復元から除外する）。
    - 参考文献（References, Bibliography）, 謝辞（Acknowledgements）, 利益相反（Conflict of Interest, Funding）, 著者情報・所属（Author Contributions, Affiliations, ORCID）
    - 付録（Appendix）※ただし本文に不可欠な注釈は残す
- **構造の一貫性**: 
    - 文脈を読み取り、適切なMarkdown見出し (#, ##, ###) を付与する。
    - **禁止**: セクション見出しにリスト記号（-）や数字書き（1.）を使用しないでください。必ずMarkdownの見出し記号（#）を使用すること。
    - 特に新しいトピックや議論の切り替わりには積極的に見出しを挿入し、構造を明確にする。
    - 本文の開始地点が見出しで明示されていない場合、推測して `# Introduction` を挿入する。
    - **重要**: 一つの論文には一つの Introduction と一つの Conclusion のみを含めてください。
- 翻訳対象の限定：タイトル、要旨（Abstract）、本文、注（Footnotes/Endnotes）のみを構造化の対象とする。
- 推論プロセス：まず最初に出力として <thought> タグ内で見出し構成案を考えてから、本文（Markdown）を出力してください。
</rules>

<input>
{text}
</input>
"""

# --- Skill: ContentSummarizer (要約) ---
SUMMARY_PROMPT = """あなたは文化人類学を専門とするシニア・リサーチャーです。
入力されたMarkdownドキュメントを精査し、各節の論理展開を Chain of Thought（段階的思考）を用いて詳細に抽出した上で、Workflowy（アウトライナー）に貼り付けるための学術的レジュメを作成してください。

<Goals>
*指定された部分の内容を学術的な視点から精緻に要約し、読者がその論理構造を深く理解できるようにする。
*文化人類学の文脈における文献の重要性を明らかにする。
*各節の議論の積み重ねを明示し、全体の結論に至るプロセスを再現する。
</Goals>

<elements>
a) リサーチ・クエスチョン: この文献において著者がどのような『問い』を立てているかを明確に記述する。
b) 核心的主張（Thesis）: 先行研究や既存のパラダイムに対し、指定された部分がどのような独自の貢献をしているか、および最終的な結論を記述する。
c) 各節の主張とその根拠: 節ごとに中心的主張を特定する。その主張を支える論理的ステップ（Chain of Thought）を段階的に明示し、どのような証拠や議論が用いられているかを詳述する。
</elements>

<rules>
- インデント（スペース4つ）のみで階層を表現し、Markdownの見出し記号（#）は絶対に使わない。
- 全ての行を「- 」（ハイフンとスペース）で始める。
- 以下の構成を含める：
    - 論文タイトル、リサーチ・クエスチョン、核心的主張（Thesis）。
    - 各セクションの論理展開（Chain of Thought）。
- 日本語2000〜3000字程度の詳細な内容。
- 導入や挨拶は不要。
</rules>

<example>
- 論文タイトル: [タイトル]
    - リサーチ・クエスチョン
        - この論文が問うていること...
    - 核心的主張
        - 著者の主要な主張...
    - 各セクションの中心的な主張と論理展開（Chain of Thought）
        - Introduction
            - 中心的な主張
            - 論点1...
            - 論点2...
        - 見出し1
            - 中心的な主張
            - 論点1...
            - 論点2...
        - 
</example>

<input>
{text}
</input>
"""

# --- Skill: AcademicTranslator (翻訳・辞書適用) ---
TRANSLATION_PROMPT = """あなたは学術翻訳の専門家です。
以下の要約（全体のあらすじ）と辞書を参考に、提供されたテキストを構造を維持したまま学術的な日本語へ翻訳してください。
全体の文脈を把握することで、指示代名詞や用語の不一致を防いでください。

<summary>
{summary_content}
</summary>

<rules>
- Markdown構造（# 見出し、リスト記号、太字など）を厳密に維持する。
- 専門用語、特に文化人類学の文脈に沿った用語を使用する。
- 一つの段落は一つの項目として出力し、段落途中で改行しない。
- 要約ではなく全文を忠実に翻訳する。
- <glossary> 内の指定がある場合、必ずその訳語を使用すること。
- **絶対禁止事項**: 
    - あなたの思考プロセス、上記ルール、辞書の内容、挨拶などを出力に含めること。
    - `<result>` タグの外にテキストを出力すること。
    - `<result>` タグの中にルールや辞書をコピーすること。
- **出力形式**: 翻訳結果（Markdownテキスト）のみを `<result>` タグで囲んで出力してください。
</rules>

<glossary>
{glossary_content}
</glossary>

<input>
{text}
</input>
"""

# 除外キーワード（パススルーで使う可能性があるが、基本はLLMが判断）
EXCLUDE_SECTION_KEYWORDS = [
    "references",
    "bibliography",
    "conflict of interest",
    "funding",
    "acknowledgements",
    "keywords",
    "author contributions",
    "orcid",
    "affiliations"
]