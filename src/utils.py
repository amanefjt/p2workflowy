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
    def load_glossary(csv_path: str | Path) -> str:
        """
        CSV辞書を読み込み、プロンプトに埋め込む形式に変換する。
        フォーマット例: `English Term -> Japanese Term`
        """
        path = Path(csv_path)
        if not path.exists():
            return ""

        glossary_lines = []
        with open(path, mode="r", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    term = row[0].strip()
                    translation = row[1].strip()
                    if term and translation:
                        glossary_lines.append(f"{term} -> {translation}")
        
        return "\n".join(glossary_lines)

    @staticmethod
    def markdown_to_workflowy(markdown_text: str) -> str:
        """
        MarkdownテキストをWorkflowy(OPML)貼り付け用フォーマットに変換する。
        """
        lines = markdown_text.split('\n')
        workflowy_lines = []
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 1. 見出し (#) の処理 -> 階層リストに変換
            if stripped.startswith('#'):
                level = stripped.count('#') - 1
                if level < 0: level = 0
                content = stripped.lstrip('#').strip()
                
                # Workflowy用インデント: 4スペース = 1階層
                indent = "    " * level
                workflowy_lines.append(f"{indent}- {content}")
                continue

            # 2. リストマーカー (-, *) の処理
            match = re.match(r'^(\s*)([-*]|\d+\.)\s+(.*)', line)
            if match:
                original_indent = match.group(1)
                content = match.group(3)
                spaces = original_indent.replace('\t', '    ')
                level = len(spaces) // 2
                new_indent = "    " * level
                workflowy_lines.append(f"{new_indent}- {content}")
            
            else:
                # リストでも見出しでもない行（本文など）
                # インデント付きの項目として追加
                workflowy_lines.append(f"    - {stripped}")

        return "\n".join(workflowy_lines)

    @staticmethod
    def split_into_sections(text: str, max_chars: int = 15000) -> list[dict]:
        """
        見出しを基準にテキストを分割し、意味的なまとまりを維持しながらチャンク化する。
        """
        # 1. まず見出し（#〜###）を基準にバラバラの構成単位にする
        units = []
        lines = text.split('\n')
        current_title = "Intro"
        current_lines = []

        for line in lines:
            if re.match(r'^#+\s+', line.strip()):
                if current_lines:
                    units.append({"title": current_title, "content": "\n".join(current_lines)})
                current_title = line.strip().lstrip('#').strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        
        if current_lines:
            units.append({"title": current_title, "content": "\n".join(current_lines)})

        # 2. 構成単位を max_chars に収まるようにグループ化する
        final_sections = []
        current_group_content = []
        current_group_length = 0
        current_group_title = ""

        for unit in units:
            content_len = len(unit["content"])
            
            # 単体で max_chars を超える巨大なセクションの場合
            if content_len > max_chars:
                # 溜まっているものがあれば出力
                if current_group_content:
                    final_sections.append({
                        "title": current_group_title,
                        "content": "\n".join(current_group_content)
                    })
                    current_group_content, current_group_length, current_group_title = [], 0, ""

                # 巨大セクションを強制分割
                sub_chunks = Utils.split_text_into_chunks(unit["content"], chunk_size=max_chars)
                for i, sub in enumerate(sub_chunks):
                    suffix = f" ({i+1})" if len(sub_chunks) > 1 else ""
                    final_sections.append({
                        "title": unit["title"] + suffix,
                        "content": sub
                    })
                continue

            # 現在のグループに追加できるかチェック
            if current_group_length + content_len > max_chars:
                # 溢れるので現在のグループを確定
                final_sections.append({
                    "title": current_group_title,
                    "content": "\n".join(current_group_content)
                })
                # 新しいグループを開始
                current_group_content = [unit["content"]]
                current_group_length = content_len
                current_group_title = unit["title"]
            else:
                # 追加可能
                current_group_content.append(unit["content"])
                current_group_length += content_len
                if not current_group_title:
                    current_group_title = unit["title"]
                elif len(current_group_title) < 50: # タイトルが長くなりすぎないように
                    current_group_title += f" & {unit['title']}"

        # 残りを出力
        if current_group_content:
            final_sections.append({
                "title": current_group_title,
                "content": "\n".join(current_group_content)
            })

        return final_sections

    @staticmethod
    def split_text_into_chunks(text: str, chunk_size: int = 15000) -> list[str]:
        """
        テキストを一定の文字数に近い位置（改行区切り）で分割する。
        """
        if len(text) <= chunk_size:
            return [text]
            
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end >= len(text):
                chunks.append(text[start:])
                break
                
            # 改行を探す
            newline_pos = text.rfind('\n', start + int(chunk_size * 0.8), end)
            if newline_pos == -1 or newline_pos <= start:
                # 改行が見つからない場合は強制分割
                chunks.append(text[start:end])
                start = end
            else:
                chunks.append(text[start:newline_pos + 1])
                start = newline_pos + 1
        
        return chunks
