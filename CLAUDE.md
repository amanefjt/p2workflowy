# CLAUDE.md

## プロジェクト概要

英語学術論文・専門書籍の PDF/テキストを Gemini AI で解析し、Workflowy 向けの階層 Markdown に変換するツール。CLI (`main.py`) と Web API (`server.py`) の 2 モード。

設計の全体像と「なぜこうなっているか」は `docs/ARCHITECTURE.md`（人間・AI 双方向けの設計解説）。本ファイルは Claude Code 向けの運用リファレンスで、**コードや `ls` から分かることは書かず、ハマりどころだけを書く**。

## セットアップ / コマンド

必須環境変数は `GEMINI_API_KEY`（または `GOOGLE_API_KEY`）。`.env.example` をコピーして設定する。`APP_ADMIN_PASSCODE` は Web 版で管理者がサーバー側キーを使う場合のみ。

自明でないフラグ:

```bash
python3 main.py data/book.pdf --book                       # 書籍モード
python3 main.py data/paper.txt --lite                      # 低コスト検証（Lite モデル固定）
python3 main.py data/paper.pdf --session <id> --resume 4   # フェーズ 4 から再開
```

テストは `python3 -m pytest tests/unit/ -v`。

## パイプラインのハマりどころ

5 フェーズ構成（Phase 1 前処理 → 2 DNA 抽出 → 3 構造化 → 4 翻訳 → 5 出力）とモジュール対応は `docs/ARCHITECTURE.md` §2 を参照。中間状態は `state/<session_id>/phaseN_*.json` に永続化され、`--resume <N>` で任意フェーズから再開できる。**この永続化互換性を壊さないこと**（`from_dict()` が未知キーを無視するのはそのため）。

- **責務境界**: フェーズのファサード（`core/phaseN_*.py`）はオーケストレーション専任。アルゴリズムは `core/engine/<pN_*>/` に閉じ込める。`main.py` はエントリーポイント専任。
- **Phase 1 の実ルート**: `run_phase1_unified()` が PDF/テキストを自動判定し、PDF は Docling / VLM / 物理抽出のいずれかを選ぶ。実際に使われたルートは `phase1_route.json` に記録される。**Phase 3 は `pdf_mode` の指定値ではなくこの実ルートを見て構造化方式を切り替える**ので、構造化の挙動を調べるときはまず `phase1_route.json` を見る。
- **テキスト分割の自動判定**: `\n\n` 分割後のチャンク数が `\n` 分割後の 1/10 未満なら Acrobat 形式（1 行 = 1 段落）と判定して `\n` 分割に切り替える。
- **`intro_pre_heading`**: Phase 2 の DNA に含まれる「最初の節タイトル前の見出しなし序論」の範囲。Phase 3 が見出しなし Introduction を分離するために使う。
- **`[Unlabeled Section]` は仕様であってバグではない**: Abstract 直後に見出しのない Introduction が続くパターン（NST 論文等）の正しい出力。問題なのは、本来存在する節タイトルが検出されず前セクションに吸収される場合だけ。
- **書籍モード**: `core/book_manager.py` が入口。章ごとに `run_pipeline()` を呼び、統合は `core/engine/p3_structure/state_integrator.py`。`BookManager` は `run_pipeline()` の戻り値（出力パス）を `chapter_sessions[].output_paths` で渡す — **パスを推定して組み立てない**。
- **書籍モードの章並列化**: CLI (`main.py --book`) は**無料キーが2本以上 `configure()` されている場合のみ**章を `ThreadPoolExecutor` で並列処理する（それ以外は常に完全直列）。並列時は章スレッドごとに `KeyRotator.restrict_to()` でキーを1本ずつ排他割り当てし（キープールは `queue.Queue`、キー本数が並列度の自然な上限）、統合順序は完了順ではなく `[None]*N` をインデックスで埋めて本の並び順を維持する。**Web版 (`server.py`) は `key_rotator.configure()` を一切呼ばないため常に直列のまま**（挙動不変）。並列度は `--book-concurrency`（既定値: 無料キー本数と処理対象章数の小さい方、`1` で強制直列）。詳細・設計判断は `docs/model_optimization.md` §10。
- **プロンプトのキャッシュ**: `core/coreprompts.json` は `@lru_cache` されるため、**変更後はプロセス再起動が必要**。
- **モデル設定**: `core/coreprompts.json` の `DEFAULT_MODEL*` が runtime の正本。Gemini API 共通知識（モデル一覧・無料枠・廃止情報）は `docs/gemini_models.md`（`~/Code/shared/` からの同期物、**直接編集禁止**）、本プロジェクト固有のルーティング・ベンチマークは `docs/model_optimization.md`。実装とドキュメントが食い違う場合はドキュメント側を直す。
- **TierManager（シングルトン）**: 429/503 で自動的に FREE ティアへダウンシフトする自己修復機構。触る場合は paper/book 両モードで回帰確認する。
- **Phase 4 並列数**: デフォルト `max_concurrent_sections=8`。直列化（=1）は実測で大幅に遅く、避けること。**これは「バッチ」ではなく「セクション」の同時実行数**なので、セクション数の少ない論文では 8 を超えて上げても効かない（AL 論文=9セクションで 16/24 は改善なし）。根拠と実測値は `docs/model_optimization.md` §2・§7・§8。
- **無料枠キーのラウンドロビン**: CLI は `GEMINI_API_KEY_FREE_1`〜`_4`（**それぞれ別 GCP プロジェクト必須**）を、FREE tier かつユーザーがモデル未指定のときだけ「キー × Lite モデル」の2軸で能動的に分散する（`KeyRotator.pool_keys()` ＋ `_pick_batch_target()`/`_pick_page_target()`、`docs/model_optimization.md` §8）。**プロアクティブな分散（`key_pinned`/`model_pinned` で固定）とリアクティブな 429 フォールバック（`KeyRotator.advance()`/`ModelRotator.advance()`）を混ぜないこと**が設計の要。PAID tier・モデル明示指定時は従来どおり単一キー/単一リミッタで、挙動は一切変わらない。

## 設計原則

- **入力ルーティングの判断優先順位**: 書籍は書籍単位で 1 回だけ判定する（①ユーザーの `pdf_mode` 明示指定 → ②見開きスキャン = VLM → ③デジタル PDF (`is_docling_viable()`) = Docling → ④それ以外 = VLM）。論文 PDF は Phase 1 が同じ優先順位で 1 文書ごとに判定する。VLM 経路内では `VLM の論理役割判断 > 物理証拠（フォント・座標） > 幾何的ヒント`。OCR 補正は「VLM が特定した位置の Native テキストで肉付けする」方針を守る。
- **出力形式の不変条件**: `_p2.md` / `_p2.txt` が標準。Workflowy では英語ブロックを親子ネスト、日本語ブロックを並列展開する非対称階層を維持する。`References` 系セクションは除外し、`Appendix` は保持する。注釈ノードは言語ブロック末尾へ再配置する。
- **設計の正本はコード**: 判断根拠は常に `core/` の現行実装を優先する。本ファイルや `docs/ARCHITECTURE.md` と矛盾する場合はコードを信じ、ドキュメント側を直す。
- **機密ファイル**: `.env` など機密情報を含みうるファイルは不用意に読み書きしない。

## テスト資産（`data/input/`）

理想出力（`*_p2.txt` 等）が同梱されており、構造変更の回帰確認に使う。`paperplain/{AL,NST}/` がテキスト入力版、`paperpdf/{AL,NST}/` が PDF 版（**`paperpdf/NST/` が最高品質の参照基準**）、`Booksample/` は書籍 PDF 3 冊（理想出力なし）。

## 変更管理

仕様変更や判断根拠は `docs/management/requirements_log.md`、不具合の原因・再現手順・対策は `docs/management/troubleshooting_log.md` に追記する（`core/` を含むコミットで追記漏れがあれば `.claude/hooks/check_management_logs.sh` が注意喚起する）。

- 完了宣言前の構造検証: `golden-verification` skill
- 構造・翻訳まわりのデバッグ手順: `p2workflowy-debug` skill

## デプロイ

Hugging Face Spaces（Docker）向け。ポート `7860` で `uvicorn` を起動（`Dockerfile` 参照）。
