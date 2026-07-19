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

        照合は _matches_heading() による行単位の一致で行う。
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
            title_lower = title.lower()

            # 探索範囲: 論理ページ前後を広めに取り可変オフセットに対応
            search_start = max(0, logical_page - 5)
            search_end = min(total_pages - 1, logical_page + 49)

            best_phys = None
            for phys_idx in range(search_start, search_end + 1):
                raw_page = doc[phys_idx].get_text("text")

                # 目次ページをスキップ（3章以上のタイトルを含むページ＝TOC）
                if self._is_toc_page(raw_page, all_titles):
                    continue

                if self._matches_heading(raw_page, norm_title, title_lower):
                    best_phys = phys_idx
                    break

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

    def _matches_heading(self, page_text: str, norm_title: str, title_lower: str) -> bool:
        """ページが章見出しページかどうかを行単位で判定する。

        章見出しはページ冒頭の行に単独で現れる（本文の句中に埋め込まれない）。
        行単位の照合により、短いタイトル（"Methods" 等）が本文中に出現しても
        誤ヒットしない。

        Pass 1: 1行単位の照合（行全体または行頭が norm_title と一致）
        Pass 2: 冒頭5行を結合して照合（見出しと副題が別行に分割されているケース）
                例: TOC="Introductions: The Compulsion of Relations" に対し
                    実ページが "Introductions" + "The Compulsion of Relations" の2行
        """
        lines = page_text.split("\n")

        # Pass 1: 1行単位
        for line in lines[:15]:
            stripped = line.strip()
            if not stripped:
                continue
            line_norm = self._normalize_title(stripped)
            if norm_title:
                if line_norm == norm_title or line_norm.startswith(norm_title + " "):
                    return True
            else:
                if title_lower and title_lower in stripped.lower():
                    return True

        # Pass 2: 冒頭5行を結合（複数行に分かれた見出し）
        # 4語以上のタイトルにのみ適用。短いタイトルは本文にも頻出するため
        # 結合マッチは誤ヒットリスクが高い。
        # 例: "Introductions: The Compulsion of Relations"（5語）→ 適用
        #     "Methods"（1語）や "Power and Politics"（3語）→ 適用しない
        if norm_title and len(norm_title.split()) >= 4:
            joined = " ".join(l.strip() for l in lines[:5] if l.strip())
            joined_norm = self._normalize_title(joined)
            if norm_title in joined_norm:
                return True

        return False

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
        t = re.sub(r'^(?:Chapter|CHAPTER|Part|PART|Section|SECTION)\s+[\dIVXivx]+\s*[.:]?\s*', '', text)
        t = re.sub(r'^[\dIVXivx]+[.:]?\s+', '', t)
        t = re.sub(r'[^\w\s]', ' ', t)
        return ' '.join(t.lower().split())

    def _extract_toc(self, doc: fitz.Document) -> List[Dict[str, Any]]:
        """LLM を用いて PDF から TOC を抽出・整理する（論理ページ番号を返す）。

        テキスト抽出が不十分（章が 2 件以下）の場合は VLM フォールバックを起動する。
        これはスキャン PDF で TOC ページが OCR されていないケースに対応する。
        """
        text_samples = ""
        for i in range(min(15, len(doc))):
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
        最初の 10 ページを画像化して Gemini Flash に渡す。
        """
        try:
            from PIL import Image as PILImage
        except ImportError:
            print_log("  [Splitter] PIL が見つかりません。VLM フォールバックをスキップします。")
            return []

        from core.llm_client import call_gemini, get_default_model

        images = []
        for i in range(min(10, len(doc))):
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
