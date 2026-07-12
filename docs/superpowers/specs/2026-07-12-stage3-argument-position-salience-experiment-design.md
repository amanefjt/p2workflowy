# Stage 3（argument_tree / 論証位置レイヤー）：salience 覗き見実験

**ステータス**: 設計確定・実験実装対象（1 回の A/B で継続 or 棚上げを判断）
**起案日**: 2026-07-12
**起案者**: shufujita（brainstorming via Claude Opus）
**位置づけ**: `2026-07-10-translation-context-architecture-design.md`（正本・4 層モデル）の **Stage 3 方針（L140-144）を置換**する。正本の「LLM 生成 argument_tree ＋スキーマ妥当性実験先行」を、**"スキーマが作れるか"ではなく"効くのか"を先に安く測る** salience 実験に尖らせた版。
**関連**: `translation_review_checklist.md`（D. 論証の保存）/ `2026-07-10-translation-context-research-notes.md` §3（Lost in the Middle）/ [[project_book_mode_resume_prompts]]

---

## Context：なぜ正本の「LLM 生成 argument_tree」を作らないか

正本 Stage 3 は layer ③「論証位置」を、章/論文ごとに LLM 生成する構造化 argument_tree（節ID→論証機能/役割/節間関係）として構想していた。起案時にコードを確認した結果、**翻訳品質への期待値は Stage 1・2 より低い**と判断した。

### 1. レジュメが既に同じ内容を運んでいる
`SUMMARY_PROMPT_ronbun` の「# 3. 各セクションの展開」は、節ごとの中心的主張・論理ステップ・**節間の接続**まで記述するよう指示されており、その全文（NST 実測 9,461 字）が layer ① として毎バッチ注入済み。argument_tree が"論証上の位置"として足そうとしている情報は、既に翻訳プロンプトに届いている。

### 2. 文献的にも論証構造は翻訳品質のレバーとして挙がっていない
research-notes の結論は「局所的結束＝連続ウィンドウ、遠方の一貫性＝用語台帳」。大域的な論証構造が訳質を上げるという先行例は見つからなかった。layer ③ は 4 層で唯一、翻訳目的での実証的裏付けが弱い（強い動機だった"論証地図＝精読支援"は深読モードごと棚上げ済み）。

### 3. 翻訳 LLM は既に「今どの節か」の手がかりの大半を持つ（コード実測）
`translate_batch`（`core/llm_client.py`）の `.format()` に入るのは `glossary_content` / `resume_content` / `previous_translation` / `chunk_json` の 4 つのみ。`section_name` は引数として最後まで渡ってきているのに **メトリクス記録に使うだけでプロンプトに入っていない**（`llm_client.py:500` 付近）。それでもモデルは節を概ね把握できる：

- **節の最初のバッチ**: 節見出しチャンク（role h1/h2）が `source_chunks` に含まれ、モデルは見出しを見ている。
- **節の途中バッチ**: `format_previous_translation` が `role=="p"` のみ集めるため見出しは窓外だが、直前段落の話題連続性で"何を論じているか"は事実上伝わる。

結論：この状態で argument_tree を足しても salience（前面化）以上の新情報はほぼ無く、その salience の効きも「見出しが既に見えている」ぶん小さい。**YAGNI 寄り**。ただし検証コストが極小なので、1 回だけ最も強い安価版を試して畳む。

---

## 実験仮説

長いレジュメの中の**"現在節の該当スライス"をバッチ末尾に前面化**すると、途中バッチでの論証位置の見失いが減り、訳の一貫性（特に節境界・論証の流れ）が上がる — *かもしれない*。**事前 EV は低い**と明言する。効かなければ layer ③ は棚上げ。

---

## 最小変更（新 LLM 呼び出しゼロ・既存資産の再配線のみ）

1. **`core/coreprompts.json` の `TRANSLATION_PROMPT`**: 条件付きスロット `{current_position}` を 1 つ追加。置き場所は **`<source_chunks>` の直前**（recency 最大化＝Lost-in-the-Middle 回避、research-notes §3 準拠）。`<current_position>...</current_position>` タグで囲む。`@lru_cache` のため変更後プロセス再起動。

2. **`core/engine/p4_translate/prompt_builder.py`**: 現在地ポインタを組む小関数を追加。
   - 既に渡ってきている `section_name`（現状メトリクスに捨てているもの）を明示。
   - `resume_content` の「# 3. 各セクションの展開」（書籍モードは章レジュメの節見出し部）から `## {section_name}` に一致するスライスを **決定的に（LLM なし）** 抜き出して添える。
   - マッチング：見出し行の正規化（前後空白・大小・角括弧除去）した完全一致を第一とし、無ければ節名のみ、それも無ければ空文字（＝現行動作に縮退）。
   - 出力例:
     ```
     # 現在地（この節の論証上の役割）
     節: Secularism and the Body
     （レジュメより）本節は前節の世俗主義論に一旦譲歩した上で、身体経験を反例として提示し理論の再定式化へ橋渡しする…
     ```

3. **`core/llm_client.py::translate_batch`**: `.format()` に `current_position=...` を配線。`section_name`・`resume_content` は既に届いているので **引数追加は不要**（builder をこの関数内から呼ぶ）。空なら `"なし"` に縮退。

### スコープ外（この実験では作らない）
- LLM 生成の構造化 argument_tree / `phase2_argument_tree.json`（正本の当初案）。
- 節単位の条件"生成"（本実験は全節でレジュメスライスを機械抽出するだけ）。
- 書籍モードの章横断的な論証位置。まず論文 NST で効くかを見る。

---

## A/B（1 回だけ）

- **対象**: NST（論文・テキスト入力）。
- **ベースライン**: 現行 Stage 2 の NST 出力（armB_hybrid / 最新 `_p2.md`）。
- **変更版**: 上記を入れて NST を完走 → `_p2.md`。
- **評価**: ユーザーが比較読み 1 回。`translation_review_checklist.md` の **D. 論証の保存** を重点。自動メトリクス不使用（2026-05 原則）。
- **モデル条件**: ハイブリッド固定（`DEFAULT_MODEL=lite` / `DEFAULT_MODEL_RESUME=3.5-flash`）。Stage 2 と同条件で文脈変更のみを切り分ける。

---

## 判断ルール

- **明確に良い**（論証の流れ・節境界の一貫性）→ formalize：正本 Stage 3 節をこの salience 版で書き直し、書籍モード拡張・エッジケース（見出しマッチ失敗率・[Unlabeled Section]）を詰める。
- **差が無い / 微妙** → revert して **layer ③ を正本に"棚上げ"記録**（深読モードと同じ扱い）。翻訳コンテキストプロジェクトは Stage 2 で完結とする。
- どちらでも `requirements_log.md` に結果（比較読み所感・判断）を残す。`core/` 変更を伴うため管理ログ追記は必須。

---

## リスク / 留意

- **見出しマッチの取りこぼし**: 論文レジュメの `## 英語節タイトル` と Phase 4 の `section_name` は原則一致するが、表記揺れ・`[Unlabeled Section]`・intro_pre_heading で外れうる。外れたら静かに縮退（節名のみ / 空）。実験段階ではマッチ率をログで確認する程度でよい。
- **redundancy による希釈**: レジュメ全文＋その一部スライスの二重掲載になる。short pointer なのでトークン害は小さい想定だが、"同じ話の繰り返し"がノイズになる可能性もゼロではない（これも A/B で判定対象）。
- **1 Stage=1 変更原則**: 新しい文脈"源"は足さない（同じレジュメ内容の再前面化＋既存 section_name）ため原則と整合。
- **これは実験**: 本命は「畳む」可能性が高い。実装は revert しやすい単一 commit にまとめ、TRANSLATION_PROMPT 変更と builder 追加を最小差分に保つ。
