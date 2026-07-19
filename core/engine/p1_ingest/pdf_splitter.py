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

            if start_page > end_page or start_page < 0:
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

        紙面ページと PDF 物理ページのオフセットは前付けや図版ページにより
        章ごとに変動するため、ページ番号への依存を断ち本文照合で補正する。

        照合は _classify_match() で章扉/ランニングヘッダーを判別し、窓内の
        全候補を _score_candidate() で採点して最良のものを選ぶ（I-22）。
        本文中の「see Chapter 5」や「methods were applied」等との誤ヒットを防ぐ。
        照合失敗時は論理ページをフォールバックとして使用し警告を出す。
        """
        total_pages = len(doc)
        all_titles = [e.get("title", "") for e in llm_toc]
        results = []
        last_found_phys = -1

        for entry in llm_toc:
            title = entry.get("title", "")
            logical_page = int(entry.get("start_page", 1))  # 1-indexed logical

            norm_title = self._normalize_title(title)

            # 探索範囲: 論理ページ前後を広めに取り可変オフセットに対応
            search_start = max(0, logical_page - 5)
            search_end = min(total_pages - 1, logical_page + 49)

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

                kind = self._classify_match(raw_page, norm_title, chapter_numeral)
                if kind is None:
                    continue

                score = self._score_candidate(raw_page, kind, chapter_numeral)
                if best_score is None or score > best_score:
                    best_phys, best_score, best_kind = phys_idx, score, kind

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
                fallback = max(0, logical_page - 1)
                if fallback <= last_found_phys:
                    # フォールバックが前章より前になる場合、順序を壊すのでスキップ
                    # そのエントリの内容は前章の範囲に吸収される
                    print_log(
                        f"  [Splitter] 警告: '{title}' が本文で見つからず、"
                        f"フォールバックP{fallback+1}が前章P{last_found_phys+1}より前のためスキップします。"
                    )
                    continue
                print_log(
                    f"  [Splitter] 警告: '{title}' が本文で見つかりません。"
                    f"論理ページ {logical_page} をフォールバックとして使用します。"
                )
                results.append({**entry, "start_page": fallback})

        return results

    # OCR で数字と誤読されやすい文字の対応表
    _OCR_DIGIT_MAP = str.maketrans(
        {'I': '1', 'l': '1', '|': '1', 'i': '1', 'r': '1',
         'O': '0', 'o': '0', 'S': '5', 'B': '8'}
    )
    _ROMAN_RE = re.compile(r'^[IVXLC]+$', re.IGNORECASE)

    def _parse_page_number(self, line: str) -> Optional[int]:
        """行を頁番号として解釈する。OCR 崩れ（'3 I'→31, 'r 72'→172）に耐える。

        数字を1文字も含まない文字列（ローマ数字 'XIII' や 'I'）は
        頁番号として扱わない。章マーカーとの誤認を防ぐため。
        """
        t = line.strip()
        if not t or len(t) > 8:
            return None
        if not any(c.isdigit() for c in t):
            return None
        normalized = t.translate(self._OCR_DIGIT_MAP).replace(' ', '')
        if normalized.isdigit() and 1 <= int(normalized) <= 9999:
            return int(normalized)
        return None

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
        self, page_text: str, norm_title: str, chapter_numeral: Optional[str]
    ) -> Optional[str]:
        """ページが章扉かランニングヘッダーかを判定する (I-22)。

        判別軸は一致行の隣接行である。裸の頁番号が隣接していれば
        ランニングヘッダー、章マーカーが隣接していれば章扉とみなす。
        exact / joined という一致の種類は判定に使わない（Naven では
        本文頁のヘッダーが exact、章扉が joined になり順位が反転するため）。
        """
        if not norm_title:
            return None

        lines = [l.strip() for l in page_text.split("\n")]
        nonempty = [l for l in lines if l]
        if not nonempty:
            return None

        # Pass 1: 行単位
        for pos, line in enumerate(nonempty[:self.HEADING_SCAN_LINES]):
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

    def _score_candidate(
        self, page_text: str, kind: str, chapter_numeral: Optional[str]
    ) -> int:
        """候補ページを章扉らしさで採点する (I-22)。"""
        score = 0
        if kind == "header":
            score += self.SCORE_HEADER_PENALTY
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
