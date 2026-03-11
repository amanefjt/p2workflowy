# タスク管理: API ティア動的最適化

## 目的
API レート制限（429 Resource Exhausted）による処理の中断を防ぎ、有料版キー（Paid）の性能を最大限に活かしつつ、無料版（Free）や混雑時でも安定して動作を継続する「インテリジェントな変速機構」を実装する。

## DoD (Done of Definition)
- [x] API ティア（PAID/FREE）を定義し、429エラーから自動判別できる。
- [x] 判別されたティアに応じて、Semaphore 数、RPM、バッチサイズを動的に変更できる。
- [x] 処理の途中で制限に達した場合、クラッシュせずに低速モードへ「ダウンシフト」して完走できる。
- [x] CLI はデフォルトでパフォーマンス優先 (Paid)、Web UI は安定優先 (Free) で動作する。

## チェックリスト
- [x] `llm_client.py`: `TierManager` の追加と 429 エラー検知ロジックの実装
- [x] `phase4_translate.py`: ティアごとのパラメータ定義と `apply_tier_settings` の実装
- [x] `main.py`: `--free` フラグの追加とティア情報の伝播
- [x] `server.py`: Web版での強制 `free` ティア設定
- [x] `docs/model_optimization.md`: コスト試算と運用ガイドの更新
