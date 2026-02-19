# -*- coding: utf-8 -*-
"""
utils.py: 汎用ユーティリティ（ファイルIO、辞書読み込み、Workflowy形式への整形）
"""
import re
import csv
from pathlib import Path

class Utils:
    """ユーティリティクラス"""



    @staticmethod
    def extract_structure_from_resume(resume_text: str) -> str:
        """
        レジュメからセクションの見出しのみを抽出し、構造化ヒント用の軽量なアウトラインを作成する。
        
        除去対象:
        - リサーチ・クエスチョン
        - 核心的主張（Thesis）
        - 各セクション内の「中心的な主張」「論理展開」
        - 箇条書きの内容
        - 参考文献、謝辞等の不要なセクション (Abstract, Notesなどは残す)
        """
        if not resume_text:
            return ""
            
        lines = resume_text.splitlines()
        outline_lines = []
        
        # フィルタリング用のキーワード
        # 分析項目
        analysis_keywords = ["リサーチ・クエスチョン", "核心的主張", "中心的な主張", "論理展開"]
        # 物理的に除外したいセクション（ただし Abstract, Notes は含めない）
        exclude_sections = ["References", "Bibliography", "Acknowledgements", "Index", "参考文献", "謝辞"]
        
        for line in lines:
            stripped = line.strip()
            
            # 見出し行（# で始まる行）のみを対象とする
            if stripped.startswith('#'):
                # 1. 分析項目を含む見出しは除外
                if any(kw in stripped for kw in analysis_keywords):
                    continue
                
                # 2. 参考文献などの不要セクションを除外
                if any(kw.lower() in stripped.lower() for kw in exclude_sections):
                    continue
                
                outline_lines.append(stripped)
        
        return "\n".join(outline_lines)

    @staticmethod
    def remove_unwanted_sections(markdown_text: str, exclude_keywords: list[str]) -> str:
        """
        Markdownテキストから、指定されたキーワードを含むセクション（見出しとその配下の本文）を削除する。
        ただし 'Abstract' と 'Notes' は除外キーワードに含まれていても保護する。
        """
        if not markdown_text:
            return ""

        # 保護すべきキーワード
        protected = {"abstract", "notes", "注釈", "抄録"}
        
        lines = markdown_text.splitlines()
        new_lines = []
        skipping = False
        current_skip_level = 0

        heading_re = re.compile(r'^(#{1,10})\s+(.*)')

        for line in lines:
            stripped = line.strip()
            header_match = heading_re.match(stripped)

            if header_match:
                level = len(header_match.group(1))
                title = header_match.group(2).lower()

                # スキップ判定
                should_exclude = any(kw.lower() in title for kw in exclude_keywords)
                is_protected = any(p in title for p in protected)

                if should_exclude and not is_protected:
                    skipping = True
                    current_skip_level = level
                    continue
                else:
                    # 新しい見出しが、スキップ中の見出しレベルと同じかそれより浅い場合はスキップ終了
                    if skipping and level <= current_skip_level:
                        skipping = False
            
            if not skipping:
                new_lines.append(line)

        return "\n".join(new_lines).strip()


    @staticmethod
    def read_text_file(path: str | Path) -> str:
        """テキストファイルを読み込む"""
        return Path(path).read_text(encoding="utf-8")

    @staticmethod
    def write_text_file(path: str | Path, content: str) -> None:
        """テキストファイルを書き込む"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def load_glossary(path: str | Path) -> str:
        """
        glossary.csv を読み込み、プロンプト挿入用のテキスト形式に変換する。
        """
        path = Path(path)
        if not path.exists():
            return ""
        
        glossary_items = []
        try:
            with open(path, mode='r', encoding='utf-8') as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 2:
                        # 用語, 訳語 の形式を想定
                        glossary_items.append(f"{row[0].strip()}: {row[1].strip()}")
            return "\n".join(glossary_items)
        except Exception as e:
            print(f"Warning: Failed to load glossary: {e}")
            return ""

    @staticmethod
    def normalize_markdown_headings(markdown_text: str) -> str:
        """
        Markdownの見出しレベルを正規化する。
        ドキュメント内の最小の見出しレベルを取得し、それが H1 (# ) になるように全体をシフトする。
        """
        if not markdown_text:
            return markdown_text

        lines = markdown_text.splitlines()
        
        # 最小の見出しレベルを探す
        min_level = 10
        for line in lines:
            match = re.match(r'^(#{1,10})\s', line)
            if match:
                level = len(match.group(1))
                if level < min_level:
                    min_level = level
        
        # 見出しがない場合はそのまま返す
        if min_level == 10:
            return markdown_text
            
        # オフセット（例：最小が ## (2) なら、オフセットは 1）
        offset = min_level - 1
        
        normalized_lines = []
        for line in lines:
            match = re.match(r'^(#{1,10})\s+(.*)', line)
            if match:
                original_hashes = match.group(1)
                content = match.group(2)
                current_level = len(original_hashes)
                
                # レベルを調整（最小 1）
                new_level = max(1, current_level - offset)
                normalized_lines.append(f"{'#' * new_level} {content}")
            else:
                normalized_lines.append(line)
        
        return "\n".join(normalized_lines)

    @staticmethod
    def markdown_to_workflowy(markdown_text: str) -> str:
        """
        Markdown形式のテキストをWorkflowy形式（インデント付きリスト）に変換する。
        2スペースずつのインデントを採用し、H1-H10の見出しレベルおよびネストされたリストに対応する。
        """
        if not markdown_text:
            return ""

        # 見出しレベルを正規化
        normalized_text = Utils.normalize_markdown_headings(markdown_text)

        lines = normalized_text.splitlines()
        wf_lines = []
        current_header_level = 0

        # 見出しパターン (# 見出し) - H10まで対応
        heading_re = re.compile(r'^(#{1,10})\s+(.*)')
        # リストアイテムパターン (- アイテム, * アイテム, + アイテム, 1. アイテム)
        # 行頭のスペースを保持してネストを判定する
        list_item_re = re.compile(r'^(\s*)([-*+]|\d+\.)\s+(.*)')

        for line in lines:
            if not line.strip():
                continue

            # 見出しの深さを判定
            header_match = heading_re.match(line.strip())
            if header_match:
                level = len(header_match.group(1)) # # -> 1, ## -> 2, etc.
                content = header_match.group(2)
                
                # H1 = 0, H2 = 2, H3 = 4, ... という 2スペース刻みの規則。
                indent_size = (level - 1) * 2
                wf_lines.append(f"{' ' * indent_size}- {content}")
                current_header_level = level
                continue

            # リストアイテムの処理
            list_match = list_item_re.match(line)
            if list_match:
                # Markdownの行頭スペースを取得
                md_indent = len(list_match.group(1))
                # 現在の見出しレベルの1段下をベースに、Markdownのインデントを考慮
                # Workflowy貼り付け用に `- ` に統一
                # 2スペース = 1レベル とみなし、current_header_level * 2 をベースラインにする。
                indent_size = (current_header_level * 2) + md_indent
                content = list_match.group(3)
                wf_lines.append(f"{' ' * indent_size}- {content}")
                continue

            # 通常の段落テキスト
            # Markdownでの行頭スペース（引用やネストされた段落）を考慮
            md_indent = len(line) - len(line.lstrip())
            indent_size = (current_header_level * 2) + md_indent
            wf_lines.append(f"{' ' * indent_size}- {line.strip()}")

        return "\n".join(wf_lines)

