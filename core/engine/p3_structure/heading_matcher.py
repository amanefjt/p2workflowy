"""
見出しの正規化・除外判定・決定論的マッチング。
Phase 3 のツリー構築とレジュメ逆引きの基盤となる純関数群。
"""

import re
from typing import List, Optional


def normalize_heading(text: str) -> str:
    """比較のために見出しを正規化する（記号、数字、ローマ数字、余分な空白を除去）。"""
    # 削りすぎた場合のフォールバック用に、記号だけ除去した元テキストを退避
    original_clean = re.sub(r'[^\w\s]', '', text).strip()
    
    # 章番号・節番号（"1. ", "Chapter 1: ", "III. ", "1.1. " 等）を除去
    # ローマ数字 (I, II, III, IV, V, VI, VII, VIII, IX, X 等) にも対応
    t = re.sub(r'^(?:Chapter\s+)?(?:[IVXLCDM]+\b|[\d\.]+)\s*[:\.]?\s*', '', text, flags=re.I)
    # 記号を除去、小文字化、空白のトリミング
    t = re.sub(r'[^\w\s]', '', t)
    norm = " ".join(t.lower().split())
    
    # 正規化の結果、空文字や2文字未満になってしまった場合は、
    # 数字やローマ数字だけのタイトルである可能性が高いため、フォールバックを使用する
    if len(norm) < 2 and original_clean:
        return " ".join(original_clean.lower().split())
        
    return norm

def is_excluded_heading(text: str, keywords: List[str]) -> bool:
    """見出しが除外キーワード（References, Appendix 等）を含むか判定。"""
    t = text.lower()
    for kw in keywords:
        if kw.lower() in t:
            return True
    return False


def match_heading(text: str, headings: List[str]) -> Optional[tuple[str, str]]:
    """
    テキストの先頭部分が既知の見出しリストのいずれかと一致するか決定論的に判定する。
    """
    lines = text.split("\n")
    first_line = lines[0].strip() if lines else ""
    if not first_line:
        return None

    norm_first = normalize_heading(first_line)
    
    for head in headings:
        norm_head = normalize_heading(head)
        if not norm_head:
            continue
            
        # 決定論的な前方一致判定（語境界のチェックを追加）
        if norm_first.startswith(norm_head):
            # norm_head の直後の文字がスペース（または末尾）であることを確認
            if len(norm_first) > len(norm_head) and norm_first[len(norm_head)] != " ":
                continue
            # --- [強化] タイトル行等の誤爆防止フィルター ---
            # マッチした見出しが行全体に対して短すぎる場合（例: タイトルの冒頭数文字に過ぎない）、
            # それは見出しではなく本文（またはタイトル）の一部とみなす。
            # 閾値: 行の長さが見出しの長さの2.2倍を超える場合はマッチを却下する。
            # 例: "Arbitrary locations: in defence..." (50枚) vs "Arbitrary locations" (19文字)
            if len(norm_first) > len(norm_head) * 2.2:
                # ただし、見出し自体が十分長い（20文字以上）場合は、
                # 連結された本文である可能性が高いのでパースを許可する。
                if len(norm_head) < 20:
                    continue

            # 一致した場合、見出しとして分離
            # 連結されていた場合（本文が1行目に残っている場合）は、単語数ベースでカット
            words = first_line.split()
            head_words_count = len(norm_head.split())
            
            # 残りのテキストを構築
            matched_remaining = " ".join(words[head_words_count:]).strip()
            if lines[1:]:
                matched_remaining += "\n" + "\n".join(lines[1:])
            
            return head, matched_remaining.strip()
            
    return None


def merge_role_headings(role_headings: List[str], resume_headings: List[str]) -> List[str]:
    """
    レジュメ由来の見出しリストに、Phase1 が role=h1/h2 と判定済みの見出しを
    フォールバックとして合成する。

    レジュメの箇条書きは要約 LLM の出力に依存し悉皆性を保証できないため
    （--lite 等の弱いモデルで末尾の見出しが漏れることがある）、Phase1 が
    既に決定論的に検出済みの見出しを補完する。resume 側の表記を優先し、
    正規化比較で重複するものは追加しない。
    """
    merged = list(resume_headings)
    seen = {normalize_heading(h) for h in merged if h}
    for h in role_headings:
        norm = normalize_heading(h)
        if norm and norm not in seen:
            merged.append(h)
            seen.add(norm)
    return merged


def extract_headings_from_resume(resume: str) -> List[str]:
    """レジュメからセクション見出し候補を抽出する。"""
    headings = []
    lines = resume.split("\n")
    for line in lines:
        line_strip = line.strip()
        # 1. 見出し行 (#, ##, ### 等) または リスト項目 (- ## ...) を抽出
        match = re.match(r'^(?:[-\*]\s*)?(#+)\s*(.*)$', line_strip)
        if match:
            title = match.group(2).strip()
            
            # 【修正】メタ見出し（構成パーツ名）の除外ロジックを部分一致から「厳密な一致」に変更
            # LLMが付与するメタ見出しのキーワードセット
            meta_keywords = {
                "リサーチ・クエスチョン", "全体のリサーチ・クエスチョン", 
                "核心的主張", "核心的主張（Thesis）", "全体の核心的主張", "中心的な主張", 
                "構成と理論的貢献", "論理展開", "詳細な論理展開", 
                "詳細な各章の論理展開", "章内の論理展開", "各節の主張とその根拠",
                "要約", "書籍全体のレジュメ", "各セクションの展開", "各章の構成と理論的貢献"
            }
            
            # 先頭の数字（"1. ", "2. "）等を取り除いたプレーンな状態で完全一致判定を行う
            clean_for_meta_check = re.sub(r'^[\d\.]+\s*', '', title).strip()
            if clean_for_meta_check in meta_keywords:
                continue
            
            # 日本語タイトル（英語タイトル）という形式の場合、英語側を優先的に抽出
            # 例: "III. 比較の比較（Comparisons of Comparisons—1）"
            # 英語見出しにハイフン、エムダッシュ、数字が含まれるケースも考慮
            en_match = re.search(r'[（\(]([a-zA-Z\s\-\u2014\d]+)[\)）]', title)
            if en_match:
                extracted_en = en_match.group(1).strip()
                if extracted_en:
                    headings.append(extracted_en)
            
            # 元のタイトルも正規化して追加（念のため日本語マッチ用）
            title_clean = re.sub(r'^[#\s]+', '', title)
            title_clean = title_clean.strip("[] ").strip()
            
            # 節番号・ローマ数字を除去（単語の境界 \b を考慮して単語の一部が削られるのを防ぐ）
            title_clean = re.sub(r'^(?:[IVXLCDM]+\b|[\d\.]+)\s*[:\.]?\s*', '', title_clean, flags=re.I)
            
            if title_clean and title_clean not in headings:
                headings.append(title_clean)
                
    return headings
