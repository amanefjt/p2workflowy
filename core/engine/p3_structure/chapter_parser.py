from pathlib import Path
from typing import List, Optional
from core.models import ChapterBoundary
from core.config import print_log


class ChapterParser:
    """
    PDF から章境界（ChapterBoundary）を抽出するオーケストレーター。
    phase3_structure.py の extract_book_chapters / apply_toc_titles / ノイズフィルタを
    クリーンな List[ChapterBoundary] API として束ねる。

    将来テキスト入力に対応する場合は、このクラスに text_parse() メソッドを追加する。
    """

    MIN_PARAGRAPHS = 5  # ノイズ章とみなす段落数しきい値

    def parse(
        self,
        input_path: str | Path,
        body_start_page: int = 1,
        toc: Optional[List[dict]] = None,
        page_offset: int = 0,
    ) -> List[ChapterBoundary]:
        """
        PDF から章境界リストを抽出して返す。

        Args:
            input_path:      PDF ファイルパス
            body_start_page: アラビア数字 1 ページ目に対応する PDF 物理ページ番号
            toc:             LLM 抽出 TOC （title / page / role の dict リスト）
            page_offset:     body_start_page - 1

        Returns:
            List[ChapterBoundary]: 章境界リスト（ノイズ除去・TOC 補正済み）
        """
        from core.phase3_structure import extract_book_chapters, apply_toc_titles

        raw: List[dict] = extract_book_chapters(
            pdf_path=str(input_path),
            body_start_page=body_start_page,
            toc=toc or [],
        )

        if toc:
            raw = apply_toc_titles(raw, toc, page_offset)

        raw = self._filter_noise(raw)

        return self._to_boundaries(raw)

    def _filter_noise(self, chapters: List[dict]) -> List[dict]:
        """本文が少なく TOC 補正もされていない章（著作権ページ等）を除去する。"""
        before = len(chapters)
        filtered = [
            ch for ch in chapters
            if ch.get("raw_title") is not None
            or len(ch.get("paragraphs", [])) >= self.MIN_PARAGRAPHS
        ]
        removed = before - len(filtered)
        if removed > 0:
            print_log(f"  [ChapterParser] ノイズ章除去: {removed} 章")
        return filtered

    @staticmethod
    def _to_boundaries(chapters: List[dict]) -> List[ChapterBoundary]:
        """dict リストを ChapterBoundary リストに変換する。"""
        import re
        result = []
        for ch in chapters:
            title = ch.get("title", "")
            is_part = bool(re.match(r"^PART\s+[IVX\d]+", title, re.I))
            result.append(ChapterBoundary(
                title=title,
                role="h2" if is_part else "h3",
                start_page=ch.get("start_page", 0),
                paragraphs=ch.get("paragraphs", []),
            ))
        return result
