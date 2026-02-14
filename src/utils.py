# -*- coding: utf-8 -*-
"""
utils.py: 汎用ユーティリティ（ファイルIO、辞書読み込み、Workflowy形式への整形）
"""
import re
import csv
import difflib
from pathlib import Path

class Utils:
    """ユーティリティクラス"""

    @staticmethod
    def clean_header_noise(raw_text: str, chapter_title: str) -> str:
        """
        OCR由来のヘッダーノイズ（章タイトルが繰り返し出現するもの）を除去する。
        
        Logic:
        1. 行ごとに分割。
        2. 各行について、 chapter_title との類似性を判定。
        3. 以下のいずれかに合致すれば削除（空行に置換）:
           - 完全一致（大文字小文字無視）。
           - Levenshtein距離が近い（類似度 0.9 以上）。
           - タイトルを含み、かつその行の 80% 以上をタイトルが占めている。
        """
        if not raw_text or not chapter_title:
            return raw_text

        lines = raw_text.splitlines()
        cleaned_lines = []
        title_lower = chapter_title.lower().strip()
        
        for line in lines:
            stripped_line = line.strip()
            line_lower = stripped_line.lower()
            
            if not stripped_line:
                cleaned_lines.append(line)
                continue

            # 1. 完全一致
            if line_lower == title_lower:
                cleaned_lines.append("")
                continue

            # 2. Levenshtein距離（difflib.SequenceMatcher）による類似度判定
            similarity = difflib.SequenceMatcher(None, line_lower, title_lower).ratio()
            if similarity >= 0.9:
                cleaned_lines.append("")
                continue

            # 3. タイトルを含み、かつその行の一定割合（70%）以上を占めているか
            # 例: "Page 45 Introduction" (Introduction がタイトル)
            if title_lower in line_lower:
                # スペースを除去して純粋な文字数で比較
                clean_title_len = len(title_lower.replace(" ", ""))
                clean_line_len = len(line_lower.replace(" ", ""))
                if clean_line_len > 0 and (clean_title_len / clean_line_len) >= 0.7:
                    cleaned_lines.append("")
                    continue

            cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    @staticmethod
    def sanitize_structured_output(markdown_text: str) -> str:
        """
        AIが生成した構造化Markdownテキストをサニタイズする（安全装置）。
        
        Logic:
        1. 2回目以降の H1 (# Title) を削除。
        2. 文中（30行目以降）に出現する不自然な ## Introduction を削除。
        3. 連続する同一または酷似した H2 見出しを削除（チャンク分割の境界での重複対策）。
        4. 構造化フェーズ（英語出力）において混入した日本語（AIのメタ発言など）を含む行を削除。
        """
        if not markdown_text:
            return markdown_text

        lines = markdown_text.split('\n')
        is_first_header_found = False
        last_h2_content = ""
        cleaned_lines = []

        # 日本語が含まれているか判定する正規表現
        jp_pattern = re.compile(r'[一-龠ぁ-ゔァ-ヴー]')

        for i, line in enumerate(lines):
            stripped_line = line.strip()
            if not stripped_line:
                cleaned_lines.append(line)
                continue

            # 4. 日本語混入の排除（構造化フェーズは英語のみ期待されるため）
            if jp_pattern.search(stripped_line):
                print(f"  [Post-process] Removed line containing Japanese: {stripped_line}")
                continue

            # 5. 英語のメタ発言（「Final check」「Corrected:」等）を排除
            # 行の先頭が特定のキーワードで始まる場合、かつ Markdown の構造を持たない場合に削除
            meta_keywords = [
                "final check", "corrected:", "removed:", "fixed:", "note:", 
                "here is the structured text", "based on the outline",
                "i have cleaned up", "added headings", "de-hyphenated"
            ]
            is_meta = False
            for kw in meta_keywords:
                if stripped_line.lower().startswith(kw):
                    # Markdownの見出しやリスト記号で始まる場合は本文の可能性があるため除外
                    if not re.match(r'^(#|[-*+]|\d+\.)', stripped_line):
                        is_meta = True
                        break
            
            if is_meta:
                print(f"  [Post-process] Removed English meta-commentary: {stripped_line}")
                continue

            # 1. H1 (# Title) の処理
            if stripped_line.startswith('# '):
                if not is_first_header_found:
                    is_first_header_found = True
                    cleaned_lines.append(line)
                else:
                    print(f"  [Post-process] Removed redundant H1 header: {stripped_line}")
                    continue
            
            # 3. H2 重複の処理
            elif stripped_line.startswith('## '):
                h2_content = stripped_line.lstrip('#').strip().lower()
                # 直前のH2と同一、または不自然な Introduction の処理
                if h2_content == last_h2_content:
                    print(f"  [Post-process] Removed duplicate H2 header: {stripped_line}")
                    continue
                
                # 2. 文中の不自然な ## Introduction の処理
                if h2_content == 'introduction' and i > 30:
                    print(f"  [Post-process] Removed suspicious mid-text Introduction: {stripped_line}")
                    continue
                
                last_h2_content = h2_content
                cleaned_lines.append(line)
            
            else:
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines)

    @staticmethod
    def sanitize_translated_output(text: str) -> str:
        """
        翻訳結果からAIのメタ発言やひとりごと、解説などを除去する。
        """
        if not text:
            return text
        
        # 1. AIが良く使う「ひとりごと」の開始・終了パターンを削除
        patterns = [
            r'^[は承]い[、。]?(?:翻訳しました|承知いたしました)。?$',
            r'^(?:以[下上]が)?翻訳結果です[。]?(?:どうぞ。?)?$',
            r'^どうぞ[。]?$',
            r'^ご提示いただいた.*?について、.*?$',
            r'^\[Context\]や\[Glossary\]を確認しましたが、.*?$',
            r'^原文の内容に集中して翻訳を行いました。?$',
            r'^翻訳にあたり、.*?$',
            r'^このセクションには.*?が含まれています。?$',
            r'^申し訳ありませんが、.*?$',
            r'^おっしゃる通り、.*?$',
            r'^承知いたしました。?$',
            r'^ここから翻訳結果です[:：]?',
            r'^---+$', # 区切り線
        ]
        
        lines = text.splitlines()
        cleaned_lines = []
        
        for line in lines:
            stripped = line.strip()
            # パターンにマッチするかチェック
            is_meta = False
            for p in patterns:
                if re.match(p, stripped):
                    is_meta = True
                    break
            
            if is_meta:
                print(f"  [Post-process] Removed meta-line: {stripped}")
                continue
            
            cleaned_lines.append(line)
            
        result = "\n".join(cleaned_lines).strip()
        
        # 2. 非常に長い「自問自答」の痕跡（特定のキーワードが密集している場合など）は、
        #    Markdown構造が壊れている可能性が高いが、ここでは行単位の削除に留める。
        #    もし全体が会話調（例：最初から最後まで「私は〜」で解説している等）の場合は
        #    検知が難しいが、プロンプトの強化で対応する。
        
        return result

    @staticmethod
    def process_uploaded_file(uploaded_file) -> str:
        """StreamlitのUploadedFileを読み込んで文字列として返す"""
        if uploaded_file is None:
            return ""
        # バイト列を文字列に変換
        return uploaded_file.getvalue().decode("utf-8")

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
    def normalize_markdown_headings(markdown_text: str) -> str:
        """
        Markdownの見出し階層を正規化する。
        例: いきなり ## から始まっていたら # に昇格させる。
            # の次に ### が来たら ## に修正する。
        """
        lines = markdown_text.split('\n')
        normalized_lines = []
        
        # 文書内で使用されている最小の見出しレベルを探す
        min_level = 100
        for line in lines:
            match = re.match(r'^(#+)\s', line)
            if match:
                level = len(match.group(1))
                if level < min_level:
                    min_level = level
        
        # 見出しが見つからなかった場合はそのまま返す
        if min_level == 100:
            return markdown_text

        # 補正値（最小レベルが ## (2) なら、-1 して # (1) にする）
        offset = min_level - 1

        for line in lines:
            match = re.match(r'^(#+)\s+(.*)', line)
            if match:
                original_hashes = match.group(1)
                content = match.group(2)
                current_level = len(original_hashes)
                
                # レベルを補正（最低1）
                new_level = max(1, current_level - offset)
                
                # 再構築
                normalized_lines.append(f"{'#' * new_level} {content}")
            else:
                normalized_lines.append(line)
        
        return "\n".join(normalized_lines)

    @staticmethod
    def markdown_to_workflowy(markdown_text: str) -> str:
        """
        MarkdownテキストをWorkflowy(OPML)貼り付け用フォーマットに変換する。
        """
        # ★ ここでまず正規化を行う
        markdown_text = Utils.normalize_markdown_headings(markdown_text)

        lines = markdown_text.split('\n')
        workflowy_lines = []
        current_level = 0 # 現在の見出しレベルを追跡
        
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 1. 見出し (#) の処理 -> 階層リストに変換
            if stripped.startswith('#'):
                level = stripped.count('#') - 1
                if level < 0: level = 0
                current_level = level
                content = stripped.lstrip('#').strip()
                
                # Workflowy用インデント: 4スペース = 1階層
                indent = "    " * current_level
                workflowy_lines.append(f"{indent}- {content}")
                continue

            # 2. リストマーカー (-, *) の処理
            match = re.match(r'^(\s*)([-*]|\d+\.)\s+(.*)', line)
            if match:
                original_indent = match.group(1)
                content = match.group(3)
                spaces = original_indent.replace('\t', '    ')
                # 内部的なリストの深さを考慮 (2スペース1段階)
                list_internal_level = len(spaces) // 2
                
                # 見出しレベル + 1 をベースにネスト
                new_level = current_level + 1 + list_internal_level
                new_indent = "    " * new_level
                workflowy_lines.append(f"{new_indent}- {content}")
            
            else:
                # リストでも見出しでもない行（本文など）
                # 常に見出しの1段階下に配置
                body_level = current_level + 1
                new_indent = "    " * body_level
                workflowy_lines.append(f"{new_indent}- {stripped}")

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

    @staticmethod
    def sanitize_filename(name: str) -> str:
        """
        文字列をファイル名として安全な形式に変換する。
        記号を除去し、前後の空白を削除。
        """
        if not name:
            return "output"
        # ファイル名に使えない文字を除去
        # \ / : * ? " < > |
        sanitized = re.sub(r'[\\/*?:"<>|]', "", name)
        # 前後の空白を削除
        sanitized = sanitized.strip()
        # 非常に長いタイトルへの対応 (OSの上限を考慮しつつ切り詰め)
        if len(sanitized) > 200:
            sanitized = sanitized[:200]
        return sanitized
