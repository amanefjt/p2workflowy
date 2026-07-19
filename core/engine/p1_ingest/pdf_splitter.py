import fitz
import json
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.llm_client import call_gemini, get_default_model
from core.config import print_log, PROJECT_ROOT

class PDFSplitter:
    """PDF を目次(TOC)に基づいて章ごとに分割する。"""

    # OCRManager と共通のキャッシュファイルを使用
    CACHE_PATH = PROJECT_ROOT / "state" / "vlm_cache.json"

    # --- Route 2 (outline) 妥当性検査 (I-24) ---
    OUTLINE_MIN_PAGES_PER_CHAPTER = 3
    OUTLINE_LABEL_SEQ_RATIO = 0.5

    # --- Route 3 TOC ページ探索 (I-25) ---
    TOC_SEARCH_PAGES = 30
    TOC_SAMPLE_PAGES = 8
    TOC_FALLBACK_PAGES = 15

    # --- コンテンツスキャンの判定 (I-22) ---
    HEADING_SCAN_LINES = 15
    JOINED_SCAN_LINES = 5
    SCORE_HEADER_PENALTY = -100
    SCORE_CHAPTER_MARKER = 30
    SCORE_SPARSE_PAGE_CHARS = 1500
    SCORE_SPARSE_BONUS = 20

    # --- 局所オフセット救済の頁番号妥当性検査 ---
    # ランニングヘッダー付近で読んだ数字が「その章の印刷頁番号」として
    # 妥当かを論理頁との差で判定する許容幅。章扉近傍のランニングヘッダー
    # は論理頁のごく近傍の値を持つ（corfra 'Knowing' は差2、Naven の
    # verso ヘッダーは差1）のに対し、隣接する裸の章番号を誤読した場合は
    # 論理頁から大きく離れる（'3 Place' 誤読は差22）。この差を閾値と
    # することで、位置（同一行/次行/前行）に依らず妥当性を判定できる。
    RESCUE_PAGE_NUMBER_TOLERANCE = 10

    def __init__(self, api_key: str, model: Optional[str] = None):
        self.api_key = api_key
        # model_optimization.md に基づき、TOC 解析には安定性の高いデフォルトモデルを使用
        self.model = model or get_default_model("default")
        self.cache: Dict[str, Any] = {}
        self._load_cache()

    def _load_cache(self):
        """キャッシュファイルをロードする。"""
        if self.CACHE_PATH.exists():
            try:
                self.cache = json.loads(self.CACHE_PATH.read_text(encoding="utf-8"))
            except:
                self.cache = {}

    def _save_cache(self):
        """キャッシュファイルを保存する。"""
        self.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.CACHE_PATH.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_pdf_hash(self, pdf_path: str) -> str:
        """PDF ファイルの冒頭 1MB をもとにハッシュ値を計算する。"""
        with open(pdf_path, "rb") as f:
            chunk = f.read(1024 * 1024)
        return hashlib.md5(chunk).hexdigest()

    def split(self, pdf_path: str, output_dir: Path) -> List[Dict[str, Any]]:
        """PDF を分割し、章ごとの情報（物理ページ補正済み）を返す。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)

        # Route 1: ローカル TOC ファイル（手動修正済みの最優先）
        local_toc = Path(pdf_path + ".toc.json")
        if local_toc.exists():
            print_log(f"  [Splitter] ルート: ローカル TOC ファイル ({local_toc.name})")
            try:
                llm_toc = json.loads(local_toc.read_text(encoding="utf-8"))
                toc_data = self._apply_content_scan(doc, llm_toc)
            except Exception as e:
                print_log(f"  [Splitter] ローカル TOC 読込エラー: {e}")
                toc_data = None
        else:
            toc_data = None

        # Route 2: PDF ネイティブ outline（デジタル PDF に最適・物理ページ直参照）
        if not toc_data:
            toc_data = self._get_chapters_from_outline(doc)
            if toc_data:
                print_log(f"  [Splitter] ルート: PDF outline（{len(toc_data)}章）")

        # Route 3: LLM TOC 抽出 + コンテンツスキャンで物理ページ補正
        # （可変オフセット問題を回避するため、ページ番号に頼らず本文照合する）
        if not toc_data:
            pdf_hash = self._get_pdf_hash(pdf_path)
            cache_key = f"{pdf_hash}_toc"
            if cache_key in self.cache:
                print_log(f"  [Splitter] ルート: キャッシュ + コンテンツスキャン")
                llm_toc = self.cache[cache_key]
            else:
                print_log(f"  [Splitter] ルート: LLM TOC 抽出 + コンテンツスキャン")
                llm_toc = self._extract_toc(doc)
                if llm_toc:
                    self.cache[cache_key] = llm_toc
                    self._save_cache()

            if llm_toc:
                # 層1: TOC の検算と系統的ずれの補正。
                # Route 3（LLM 抽出）のみに掛ける。Route 1（手動修正済みTOC）と
                # Route 2（ネイティブ outline・物理頁直参照）には掛けない。
                from .toc_verifier import verify_and_fix_toc
                llm_toc = verify_and_fix_toc(doc, llm_toc, self._normalize_title)
                toc_data = self._apply_content_scan(doc, llm_toc)

        if not toc_data:
            print_log("  [Splitter] 警告: TOC の取得に失敗しました。全編を単独章として扱います。")
            doc.close()
            return [{"title": Path(pdf_path).stem, "path": pdf_path, "role": "chapter"}]

        # 分割実行（toc_data の start_page はすべて 0-indexed 物理ページ）
        results = []
        target_roles = ["chapter", "preface", "introduction", "appendix"]

        for i, entry in enumerate(toc_data):
            title = entry.get("title", f"Chapter_{i+1}")
            start_page = entry.get("start_page", 0)  # 0-indexed 物理ページ
            role = entry.get("role", "chapter")

            if role not in target_roles and "Chapter" not in title:
                print_log(f"    - スキップ (Back Matter/Meta): {title} (P{start_page+1})")
                continue

            if i < len(toc_data) - 1:
                end_page = toc_data[i+1].get("start_page", 0) - 1
            else:
                end_page = len(doc) - 1

            if start_page > end_page or start_page < 0 or start_page >= len(doc):
                # C2 defence in depth: start_page が文書範囲外の場合、
                # insert_pdf() に渡すと PyMuPDF が黙って末尾頁にクランプし、
                # 無関係な頁を「章」として出力してしまう。ここで確実に弾く。
                if start_page >= len(doc):
                    print_log(
                        f"    - スキップ (開始ページが文書範囲外): {title} "
                        f"(P{start_page+1} > 総頁数{len(doc)})"
                    )
                continue

            safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
            if not safe_title:
                safe_title = f"chapter_{i+1}"
            out_filename = f"{i+1:02d}_{safe_title}.pdf"
            out_path = output_dir / out_filename

            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=start_page, to_page=end_page)
            new_doc.save(str(out_path))
            new_doc.close()

            results.append({
                "title": title,
                "path": str(out_path),
                "role": role,
                "page_range": (start_page + 1, end_page + 1),
            })
            print_log(f"  [Splitter] 抽出: {out_filename} (P{start_page+1}-{end_page+1})")

        doc.close()
        return results

    def _is_plausible_outline(
        self, entries: List[tuple], total_pages: int
    ) -> bool:
        """outline が章目次として妥当かを検査する (I-24)。

        スキャンソフトはページラベル（f1, f2, ... ）を outline として
        埋め込むことがある。これを章目次として採用すると 1頁=1章 の
        分割が発生するため、明らかに章目次でないものを棄却する。
        """
        if not entries or total_pages <= 0:
            return False

        # 指標A: 1章あたり平均頁数が少なすぎる
        pages_per_chapter = total_pages / len(entries)
        if pages_per_chapter < self.OUTLINE_MIN_PAGES_PER_CHAPTER:
            print_log(
                f"  [Splitter] outline 棄却: {len(entries)}件/{total_pages}頁 "
                f"= 1章あたり平均{pages_per_chapter:.2f}頁が下限{self.OUTLINE_MIN_PAGES_PER_CHAPTER}頁未満"
            )
            return False

        # 指標B: 連番ページラベル形式（共通接頭辞 + 数字のみ）が大半
        label_like = 0
        for title, _ in entries:
            t = title.strip()
            if re.fullmatch(r'[A-Za-z]{0,3}\d{1,4}', t):
                label_like += 1
        if (label_like / len(entries)) > self.OUTLINE_LABEL_SEQ_RATIO:
            print_log(
                f"  [Splitter] outline 棄却: 連番ページラベル形式が "
                f"{label_like}/{len(entries)} 件"
            )
            return False

        return True

    def _get_chapters_from_outline(self, doc: fitz.Document) -> Optional[List[Dict[str, Any]]]:
        """PDF ネイティブ outline から章リストを取得（物理ページ直参照）。"""
        toc = doc.get_toc()  # [(level, title, phys_page_1indexed), ...]
        if not toc:
            return None

        # まず level 1 を試し、なければ level 2
        for target_level in (1, 2):
            entries = [(title, phys) for level, title, phys in toc if level == target_level]
            if entries:
                break
        if not entries:
            return None

        if not self._is_plausible_outline(entries, len(doc)):
            return None

        return [
            {
                "title": title,
                "start_page": max(0, phys - 1),  # 1-indexed → 0-indexed
                "role": self._classify_role(title),
            }
            for title, phys in entries
        ]

    def _apply_content_scan(
        self, doc: fitz.Document, llm_toc: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """LLM の論理ページ番号をコンテンツスキャンで物理ページに補正する。

        紙面ページと PDF 物理ページのオフセットの挙動は PDF の作られ方に
        依存し、事前に予測できない。実測では見開きスキャン（corfra）は
        全巻を通じて一定の +8、組版由来のデジタル PDF（relations）は
        部扉ごとに +7→+9→+11→+13 と階段状に変動した。したがってオフセット
        を仮定せず、論理ページは探索窓のヒントとしてのみ使い、本文照合で
        位置を決める。

        照合は _classify_match() が担う。一致したタイトル行の隣接行が
        裸の頁番号ならランニングヘッダー、章マーカーなら章扉と判別する。
        窓内の全候補を _score_candidate() で採点し最良のものを選ぶ
        （first-match-wins ではない、I-22）。本文中の「see Chapter 5」や
        「methods were applied」等との誤ヒットを防ぐ。最良候補がランニング
        ヘッダーだった場合のみ _rescue_by_local_offset() が局所オフセット
        から扉頁を導出する。照合が全滅した場合は論理ページをフォールバック
        として使用し警告を出す。
        """
        total_pages = len(doc)
        all_titles = [e.get("title", "") for e in llm_toc]
        results = []
        last_found_phys = -1

        for entry in llm_toc:
            title = entry.get("title", "")
            title_lower = title.lower()
            raw_logical = entry.get("start_page")
            if raw_logical is None:
                # 層1の shift 補正で参照先が無かった章。論理頁のヒントを持たないため
                # 文書全体を探索窓とし、本文照合のみで位置を決める。
                logical_page = 1
                has_logical_hint = False
            else:
                logical_page = int(raw_logical)  # 1-indexed logical
                has_logical_hint = True

            norm_title = self._normalize_title(title)

            # 探索範囲: 論理ページ前後を広めに取り可変オフセットに対応
            if has_logical_hint:
                search_start = max(0, logical_page - 5)
                search_end = min(total_pages - 1, logical_page + 49)
            else:
                search_start = max(0, last_found_phys + 1)
                search_end = total_pages - 1

            chapter_numeral = self._extract_leading_numeral(title)

            best_phys = None
            best_score = None
            best_kind = None
            for phys_idx in range(search_start, search_end + 1):
                if phys_idx <= last_found_phys:
                    continue          # 単調性: 前章より前は採らない
                raw_page = doc[phys_idx].get_text("text")

                # 目次ページをスキップ（3章以上のタイトルを含むページ＝TOC）
                if self._is_toc_page(raw_page, all_titles):
                    continue

                kind = self._classify_match(raw_page, norm_title, chapter_numeral, title_lower)
                if kind is None:
                    continue

                score = self._score_candidate(raw_page, kind, chapter_numeral)
                if best_score is None or score > best_score:
                    best_phys, best_score, best_kind = phys_idx, score, kind

            if best_phys is not None and best_kind == "header":
                rescued = self._rescue_by_local_offset(
                    doc, best_phys, logical_page, norm_title)
                # 救済結果は探索窓内に限定する（I-22 Task4 実データ検証で判明）。
                # 誤読された印刷頁番号は _rescue_by_local_offset 内の
                # RESCUE_PAGE_NUMBER_TOLERANCE 妥当性検査で大半を排除できるが、
                # この窓ガードは二重の安全策（defence in depth）として残す。
                # 窓内に留まる場合のみ採用することで、万一妥当性検査をすり抜けた
                # 誤爆があっても大幅なずれを排除する（Knowing のような正当な
                # 救済は窓内に収まる）。
                if (
                    rescued is not None
                    and rescued > last_found_phys
                    and search_start <= rescued <= search_end
                ):
                    print_log(
                        f"  [Splitter] 局所オフセット補正: '{title}' "
                        f"物理P{best_phys+1} → P{rescued+1}"
                    )
                    best_phys = rescued

            if best_phys is not None:
                phys_display = best_phys + 1
                if phys_display != logical_page:
                    print_log(
                        f"  [Splitter] ページ補正: '{title}' "
                        f"論理P{logical_page} → 物理P{phys_display}"
                    )
                last_found_phys = best_phys
                results.append({**entry, "start_page": best_phys})
            else:
                raw_fallback = max(0, logical_page - 1)
                # C2: 論理ページが文書の総頁数を超える場合、そのまま採用すると
                # insert_pdf() に範囲外の from_page/to_page を渡すことになる。
                # PyMuPDF はこれを例外にせず黙って末尾頁へクランプするため、
                # 検知不能なまま無関係な頁（多くは索引頁）が複製される
                # （実測: PSEpdf.pdf の Chapter 9-11・Writing societies が
                # いずれも索引頁1頁のみの同一内容ファイルになった。I-27）。
                # rescued_fallback 分岐（すぐ下）は総頁数超過を既に検査して
                # いるため、この双子の分岐にも同じ上限を課す。
                fallback = min(raw_fallback, total_pages - 1)
                if fallback != raw_fallback:
                    print_log(
                        f"  [Splitter] 警告: '{title}' の論理ページ {logical_page} "
                        f"は文書の総頁数 {total_pages} を超えるため、"
                        f"フォールバックを文書末尾 P{fallback+1} にクランプします。"
                    )
                if fallback <= last_found_phys:
                    # フォールバックも前章より前になる場合、単調性のためそのまま
                    # 採用はできないが、章を結果から消してはならない（欠落防止）。
                    # 前章の直後に配置することで順序を保ったまま出力に残す。
                    rescued_fallback = last_found_phys + 1
                    if rescued_fallback >= total_pages:
                        print_log(
                            f"  [Splitter] 警告: '{title}' が本文で見つからず、"
                            f"配置可能な物理ページが残っていないためスキップします。"
                        )
                        continue
                    print_log(
                        f"  [Splitter] 警告: '{title}' が本文で見つからず、"
                        f"フォールバックP{fallback+1}も前章P{last_found_phys+1}以前のため、"
                        f"章の欠落を避けるため前章直後の物理P{rescued_fallback+1}に配置します。"
                    )
                    last_found_phys = rescued_fallback
                    results.append({**entry, "start_page": rescued_fallback})
                    continue
                print_log(
                    f"  [Splitter] 警告: '{title}' が本文で見つかりません。"
                    f"論理ページ {logical_page} をフォールバックとして使用します。"
                )
                # 単調性の watermark はこの分岐でも更新する。ここを飛ばすと
                # 後続章の探索窓が今回のフォールバック位置を考慮せず、
                # フォールバック位置より手前の頁に誤って一致しうる
                # （結果リストが非単調になり split() で章が消失する。Finding 3）。
                last_found_phys = fallback
                results.append({**entry, "start_page": fallback})

        return results

    def _parse_page_number(self, line: str) -> Optional[int]:
        """行を頁番号として解釈する。実装は page_number_map に委譲する。

        照合ループ（_classify_match / _rescue_by_local_offset）から呼ばれるため、
        挙動は従来と完全に同一でなければならない。
        """
        from .page_number_map import parse_page_number
        return parse_page_number(line)

    def _extract_leading_numeral(self, title: str) -> Optional[str]:
        """TOC タイトルの先頭章番号を取り出す（'4 Things' → '4'）。"""
        m = re.match(
            r'^\s*(?:Chap(?:ter)?\.?|Part|Section)?\s*([\dIVXLCivxlc]+)[.:]?\s+',
            title,
        )
        return m.group(1) if m else None

    def _is_chapter_marker(self, line: str, chapter_numeral: Optional[str]) -> bool:
        """行が章マーカー（'CHAPTER' / その章の番号）かを判定する。

        単なる数字を無条件に章マーカーとすると本文頁の頁番号と区別
        できないため、TOC 由来の章番号と一致する場合のみ真とする。
        """
        t = line.strip()
        if not t or len(t) > 12:
            return False
        if t.upper().rstrip('.') in ('CHAPTER', 'CHAP', 'PART'):
            return True
        if chapter_numeral:
            return t.strip('.').upper() == chapter_numeral.strip('.').upper()
        return False

    def _classify_match(
        self, page_text: str, norm_title: str, chapter_numeral: Optional[str],
        title_lower: str = "",
    ) -> Optional[str]:
        """ページが章扉かランニングヘッダーかを判定する (I-22)。

        判別軸は一致行の隣接行である。裸の頁番号が隣接していれば
        ランニングヘッダー、章マーカーが隣接していれば章扉とみなす。
        exact / joined という一致の種類は判定に使わない（Naven では
        本文頁のヘッダーが exact、章扉が joined になり順位が反転するため）。

        norm_title が空文字列になるケース（'Chapter 1' のように説明語を
        伴わない裸の章番号は _normalize_title() が章番号プレフィックスごと
        剥がすため空文字列になる）では、行単位の正規化一致が機能しない。
        この場合は title_lower による前方一致（行頭が title_lower と一致し、
        直後が英数字でない）にフォールバックする。単純な部分文字列一致では
        本文中の言及（'They are the subject of Chapter 10.'）や桁違いの
        章番号（'Chapter 1' が 'Chapter 10' に誤ヒット）を拾ってしまうため、
        行頭一致＋非英数字境界を要求する。フォールバックで一致した行も
        通常の一致行と同じ隣接判定にかけ、"title" に短絡させない。ただし
        Pass 2（複数行結合）は裸の章番号だと本文中の "chapter 1" にも
        過剰一致するため、norm_title が空の場合はスキップする。
        """
        if not norm_title and not title_lower:
            return None

        lines = [l.strip() for l in page_text.split("\n")]
        nonempty = [l for l in lines if l]
        if not nonempty:
            return None

        # Pass 1: 行単位
        for pos, line in enumerate(nonempty[:self.HEADING_SCAN_LINES]):
            if norm_title:
                line_norm = self._normalize_title(line)
                if line_norm == norm_title:
                    pass
                elif line_norm.startswith(norm_title + " "):
                    rest = line_norm[len(norm_title):].strip()
                    if self._parse_page_number(rest) is not None:
                        return "header"   # 'Knowing | 147'
                    if not self._is_chapter_marker(rest, chapter_numeral):
                        continue          # 本文行の前方一致は一致とみなさない
                else:
                    continue
            else:
                # norm_title が空（裸の章番号）: title_lower による前方一致。
                # 行頭が title_lower と一致し、直後が英数字でない場合のみ
                # 一致とみなす（本文中の言及・桁違い章番号への誤ヒット防止）。
                line_lower = line.lower()
                if line_lower == title_lower:
                    pass
                elif (
                    line_lower.startswith(title_lower)
                    and len(line_lower) > len(title_lower)
                    and not line_lower[len(title_lower)].isalnum()
                ):
                    pass
                else:
                    continue

            neighbors = []
            if pos > 0:
                neighbors.append(nonempty[pos - 1])
            if pos + 1 < len(nonempty):
                neighbors.append(nonempty[pos + 1])

            if any(self._is_chapter_marker(n, chapter_numeral) for n in neighbors):
                return "title"
            if any(self._parse_page_number(n) is not None for n in neighbors):
                return "header"
            return "title"

        if not norm_title:
            # 裸の章番号は複数行結合一致（Pass 2）を行わない。'chapter 1' の
            # ような文字列は本文中のあらゆる箇所に過剰一致してしまうため。
            return None

        # Pass 2: 冒頭数行の結合（複数行に割れたタイトル）
        #
        # 部分文字列一致にしてはならない。短いタイトルが本文を掴むため
        # （'things' は "…people are helped by things, and it is against…"
        # に含まれてしまう）。章扉はタイトルで始まる性質を使い、先頭の
        # 章マーカー（'CHAPTER' / 'XIII' / '8'）を剥がしてから前方一致を見る。
        joined_norm = self._normalize_title(" ".join(nonempty[:self.JOINED_SCAN_LINES]))
        tokens = joined_norm.split()
        while tokens and self._is_chapter_marker(tokens[0], chapter_numeral):
            tokens.pop(0)
        if tokens and " ".join(tokens).startswith(norm_title):
            return "title"

        return None

    def _rescue_by_local_offset(
        self, doc: fitz.Document, phys_idx: int, logical_page: int, norm_title: str
    ) -> Optional[int]:
        """ランニングヘッダーから局所オフセットを求め真の開始位置を導く (I-22)。

        章扉のタイトル文字がテキスト層から欠落している書籍（corfra の
        'Knowing' 章）では、どんなテキスト照合でも扉頁に到達できない。
        照合が当たったヘッダー頁の印刷頁番号 P から局所オフセット
        (phys_idx - P) を求め、TOC の論理頁に加えることで扉頁を導出する。

        印刷頁番号は一致行につき3箇所を順に見る: (1) 一致行自身の残り
        （'Knowing | 147' の corfra 形式）、(2) 直後の行（タイトルと頁番号
        が別行に分かれる形式）、(3) 直前の行。(3) が必要なのは、組版の
        見開きでは奇数頁（recto）が「タイトル→頁番号」、偶数頁（verso）
        は逆順「頁番号→タイトル」になるという交互配置の慣習があるため
        （Naven.pdf 全380頁がこの形式。verso 実測: idx31 '2\nMethods of
        Presentation\n…'）。直前行を見なければ verso 頁を全て取りこぼす。

        ただし直前行には章扉特有の罠がある。章扉は章番号が単独行で先行し
        本題が続くレイアウトが一般的（'3\nPlace\nThe difference between…'）
        で、この裸の章番号は verso の頁番号と行位置だけでは区別できない。
        位置では判別できないため、読み取った数字それぞれに対し
        RESCUE_PAGE_NUMBER_TOLERANCE 以内に logical_page があるかで妥当性
        を判定する。章扉近傍のランニングヘッダーの印刷頁番号は論理頁の
        ごく近傍にあるはずだが（corfra 'Knowing' は差2、Naven verso は
        差1）、隣接する裸の章番号を誤読した場合は論理頁から大きく外れる
        （'3 Place' 誤読は差22）。3箇所を順に試し、妥当性を満たす最初の
        候補を採用する。全て不合格なら None を返し、章扉頁の探索結果を
        そのまま維持させる（安全側のフォールバック）。

        書籍全体の頁番号マップは作らない。オフセットは PDF の作られ方に
        依存し（見開きスキャンでは全巻一定、組版由来では部扉ごとに階段状）
        大域的な抽出は書式依存で脆いため、当たった1頁のみを見る。
        """
        lines = [l.strip() for l in doc[phys_idx].get_text("text").split("\n") if l.strip()]

        for pos, line in enumerate(lines[:self.HEADING_SCAN_LINES]):
            line_norm = self._normalize_title(line)
            if not line_norm.startswith(norm_title):
                continue

            rest = line_norm[len(norm_title):].strip()
            candidates = [self._parse_page_number(rest)]
            if pos + 1 < len(lines):
                candidates.append(self._parse_page_number(lines[pos + 1]))
            if pos > 0:
                candidates.append(self._parse_page_number(lines[pos - 1]))

            for printed in candidates:
                if printed is None:
                    continue
                if abs(printed - logical_page) > self.RESCUE_PAGE_NUMBER_TOLERANCE:
                    continue
                predicted = logical_page + (phys_idx - printed)
                if not (0 <= predicted < len(doc)):
                    continue
                return predicted

            break

        return None

    def _score_candidate(
        self, page_text: str, kind: str, chapter_numeral: Optional[str]
    ) -> int:
        """候補ページを章扉らしさで採点する (I-22)。

        章マーカー加点・疎密加点は kind == "title" の候補にのみ与える。
        header にも加点すると、探索窓内の全候補がランニングヘッダーである
        場合（章扉頁のテキスト層にタイトルが載っていないスキャン等）に
        「最も疎な header」が勝ってしまい、任意性の高いページが選ばれる。
        header は常に SCORE_HEADER_PENALTY のみとすることで、全候補が
        header の窓では最も早い（=単調性上最初に見つかる）header が
        決定的に選ばれるようにする。
        """
        if kind == "header":
            return self.SCORE_HEADER_PENALTY
        score = 0
        head = [l.strip() for l in page_text.split("\n") if l.strip()][:self.JOINED_SCAN_LINES]
        if any(self._is_chapter_marker(l, chapter_numeral) for l in head):
            score += self.SCORE_CHAPTER_MARKER
        if len(page_text.strip()) < self.SCORE_SPARSE_PAGE_CHARS:
            score += self.SCORE_SPARSE_BONUS
        return score

    def _is_toc_page(self, page_text: str, all_titles: List[str]) -> bool:
        """ページが目次ページかどうかを判定する。

        目次ページは全章タイトルを列挙するため、3章以上のタイトルを含む。
        これにより、検索開始が前付け内の場合の誤ヒットを防ぐ。
        """
        page_lower = page_text.lower()
        matches = sum(1 for t in all_titles if t and t.lower() in page_lower)
        return matches >= 3

    def _classify_role(self, title: str) -> str:
        """章タイトルから role を推定する。"""
        t = title.lower()
        if any(w in t for w in ["preface", "foreword", "前書き", "はしがき"]):
            return "preface"
        if any(w in t for w in ["appendix", "付録"]):
            return "appendix"
        if "introduction" in t:
            return "introduction"
        return "chapter"

    def _normalize_title(self, text: str) -> str:
        """タイトル照合用の正規化（章番号・記号除去・小文字化）。"""
        t = re.sub(
            r'^(?:Chapter|CHAPTER|Chap\.?|CHAP\.?|Part|PART|Section|SECTION)\s+[\dIVXivx]+\s*[.:]?\s*',
            '', text)
        t = re.sub(r'^[\dIVXivx]+[.:]?\s+', '', t)
        t = re.sub(r'[^\w\s]', ' ', t)
        return ' '.join(t.lower().split())

    TOC_HEADING_RE = re.compile(
        r'^\s*(TABLE\s+OF\s+CONTENTS|CONTENTS|目\s*次)\s*$',
        re.IGNORECASE | re.MULTILINE,
    )

    def _find_toc_pages(self, doc: fitz.Document) -> List[int]:
        """目次ページを探索し、LLM に渡すページ index を返す (I-25)。

        従来は先頭15頁固定だったが、Naven.pdf のように目次が idx 16-24 に
        ある書籍では窓外となり TOC を取得できなかった。目次見出しを探索し、
        見つかった位置から後続頁を含めて返す。

        見つかった場合（discovery-hit）: 見出し検出位置は目次の開始頁だと
        分かっているため、そこから TOC_SAMPLE_PAGES（8頁）だけ読めば十分。

        見つからない場合（fallback）: 見出し正規表現が実際の目次を捉え損ねた
        （本文と結合、字間の空いた "C O N T E N T S"、頁番号が見出し行と同居
        等）可能性があり、目次の開始位置が不明なため、探索窓を広めに取る
        必要がある。従来の固定 15 頁挙動を TOC_FALLBACK_PAGES として維持し、
        LLM 自身に目次を意味的に発見させる（I-26: 8頁への一律短縮は
        以前動いていた書籍を壊す退行だったため元に戻した）。
        """
        limit = min(self.TOC_SEARCH_PAGES, len(doc))
        for i in range(limit):
            text = doc[i].get_text()
            # 先頭数行に目次見出しがあるページを目次とみなす
            head = "\n".join(text.split("\n")[:5])
            if self.TOC_HEADING_RE.search(head):
                end = min(i + self.TOC_SAMPLE_PAGES, len(doc))
                print_log(f"  [Splitter] 目次ページ検出: idx {i}-{end-1}")
                return list(range(i, end))

        return list(range(min(self.TOC_FALLBACK_PAGES, len(doc))))

    def _extract_toc(self, doc: fitz.Document) -> List[Dict[str, Any]]:
        """LLM を用いて PDF から TOC を抽出・整理する（論理ページ番号を返す）。

        テキスト抽出が不十分（章が 2 件以下）の場合は VLM フォールバックを起動する。
        これはスキャン PDF で TOC ページが OCR されていないケースに対応する。
        """
        text_samples = ""
        for i in self._find_toc_pages(doc):
            text_samples += f"--- Page {i+1} ---\n" + doc[i].get_text() + "\n"

        from core.llm_client import load_coreprompts
        prompts = load_coreprompts()
        prompt = prompts.get("TOC_EXTRACTION_PROMPT", "").replace("{text}", text_samples)

        toc = []
        try:
            from core.llm_client import call_gemini
            response = call_gemini(
                prompt,
                api_key=self.api_key,
                model=self.model,
                response_mime_type="application/json"
            )
            data = json.loads(response)
            toc = data.get("toc", [])
        except Exception as e:
            print_log(f"  [Splitter] TOC テキスト解析エラー: {e}")

        # テキスト抽出で十分な章が得られなかった場合は VLM フォールバック
        # （スキャン PDF で TOC ページが OCR されていないケースに対応）
        non_skip = [e for e in toc if e.get("role") != "skip"]
        if len(non_skip) <= 2:
            print_log(f"  [Splitter] テキスト抽出で章が {len(non_skip)} 件のみ。VLM フォールバック起動...")
            vlm_toc = self._extract_toc_vlm(doc)
            if len([e for e in vlm_toc if e.get("role") != "skip"]) > len(non_skip):
                toc = vlm_toc

        return toc

    def _extract_toc_vlm(self, doc: fitz.Document) -> List[Dict[str, Any]]:
        """ページ画像を VLM に渡して TOC を抽出する。

        テキスト抽出が失敗するスキャン PDF（見開きスキャン・白抜き文字等）に対応する。
        `_find_toc_pages` で探索したページを画像化して Gemini Flash に渡す。
        """
        try:
            from PIL import Image as PILImage
        except ImportError:
            print_log("  [Splitter] PIL が見つかりません。VLM フォールバックをスキップします。")
            return []

        from core.llm_client import call_gemini, get_default_model

        images = []
        for i in self._find_toc_pages(doc):
            pix = doc[i].get_pixmap(dpi=150)
            img = PILImage.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)

        vlm_prompt = (
            "以下の PDF ページ画像を見て、目次（Table of Contents / Contents）ページを探してください。\n"
            "目次が見つかった場合、以下の JSON 形式で各章を出力してください。\n"
            "目次がない場合は {\"toc\": []} を返してください。\n\n"
            "【出力形式 (JSON Only)】\n"
            "{\n"
            "  \"toc\": [\n"
            "    {\"title\": \"章タイトル\", \"start_page\": <目次に記載の印刷ページ番号（数値）>, \"role\": \"chapter|preface|introduction|appendix|skip\"}\n"
            "  ]\n"
            "}\n\n"
            "【role の選択基準】\n"
            "- preface: Preface / Acknowledgments / Foreword など前書き類\n"
            "- introduction: Introduction / Prologue など\n"
            "- chapter: 本編の各章\n"
            "- appendix: Appendix / 付録など\n"
            "- skip: Notes / Bibliography / Index など（翻訳不要）\n\n"
            "解説や挨拶は不要です。純粋な JSON のみを出力してください。"
        )

        content = images + [vlm_prompt]

        try:
            vlm_model = get_default_model("vlm")
            response = call_gemini(
                prompt=content,
                api_key=self.api_key,
                model=vlm_model,
                thinking_level="Low",
            )
            # コードブロック除去
            response = re.sub(r"^```[a-zA-Z]*\n?", "", response.strip())
            response = re.sub(r"\n?```$", "", response)
            data = json.loads(response)
            toc = data.get("toc", [])
            print_log(f"  [Splitter] VLM TOC 抽出成功: {len(toc)} 件")
            return toc
        except Exception as e:
            print_log(f"  [Splitter] VLM TOC 解析エラー: {e}")
            return []
