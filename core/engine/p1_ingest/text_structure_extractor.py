import json
import re
from typing import List
from core.models import RawChunk
from core.config import print_log, load_coreprompts


class TextStructureExtractor:
    """
    プレーンテキスト入力（Acrobat 等の抽出テキスト）から、LLM を使って
    セクション見出しを抽出し、対応する RawChunk に role を付与するエンジン。

    PDF の VLM OCR が担う「構造認識」を、テキスト向けに代替する。
    """

    def __init__(self, api_key: str, model: str = None):
        self.api_key = api_key
        self.model = model

    def extract_headings(self, chunks: List[RawChunk]) -> List[str]:
        """
        チャンクリストから学術論文のセクション見出しを LLM で抽出する。
        Returns: 見出し文字列のリスト（原文ママ）
        """
        from core.llm_client import call_gemini

        # コスト抑制: 先頭 120 チャンク（論文の本体構造はほぼここに収まる）
        sample = chunks[:120]
        text_for_llm = "\n\n".join(c.text for c in sample)

        prompts = load_coreprompts()
        prompt_template = prompts.get("TEXT_STRUCTURE_EXTRACTION_PROMPT", "")
        if not prompt_template:
            print_log("  [TextStructureExtractor] ⚠️ プロンプトが未定義。見出し抽出をスキップ。")
            return []

        prompt = prompt_template.format(text=text_for_llm)

        try:
            response = call_gemini(
                prompt,
                api_key=self.api_key,
                model=self.model,
                response_mime_type="application/json",
            )
            clean = re.sub(r"```(?:json)?|```", "", response).strip()
            arr_match = re.search(r"\[.*\]", clean, re.DOTALL)
            if not arr_match:
                raise ValueError(f"JSON 配列が見つかりませんでした: {clean[:80]}")
            headings = json.loads(arr_match.group(0))
            if isinstance(headings, list):
                result = [h for h in headings if isinstance(h, str) and h.strip()]
                print_log(f"  [TextStructureExtractor] {len(result)} 件の見出しを抽出しました。")
                return result
        except Exception as e:
            print_log(f"  [TextStructureExtractor] ⚠️ 見出し抽出失敗: {e}。空リストで続行。")
        return []

    def assign_roles(self, chunks: List[RawChunk], headings: List[str]) -> List[RawChunk]:
        """
        抽出した見出しリストを使って、一致するチャンクに role="h2" を付与する。
        見出しが見つからなかったチャンクは role="p" のまま。

        検出ロジック: 改行位置（\\n）で行を分割し、各行頭で見出し候補を探す。
        確認条件: 見出し直後の単語が大文字始まり（Title Case）か、行末まで見出しのみ。
        これにより1単語見出しも文中誤爆なく安全に検出できる。
        """
        if not headings:
            return chunks

        heading_pairs = [(self._normalize(h), h) for h in headings if h.strip()]
        result: List[RawChunk] = []

        for chunk in chunks:
            split = self._find_heading_at_line_boundary(chunk.text, heading_pairs)
            if split is None:
                result.append(chunk)
                continue

            before_text, head_text, after_text = split

            if before_text:
                result.append(RawChunk(
                    id=chunk.id, text=before_text, role="p",
                    seq_index=chunk.seq_index - 0.001,
                ))
            head_id = f"{chunk.id}_h" if before_text else chunk.id
            result.append(RawChunk(
                id=head_id, text=head_text, role="h2",
                seq_index=chunk.seq_index,
            ))
            if after_text:
                result.append(RawChunk(
                    id=f"{chunk.id}_b", text=after_text, role="p",
                    seq_index=chunk.seq_index + 0.001,
                ))
            if before_text or after_text:
                print_log(f"  [TextStructureExtractor] 見出し分割: '{head_text[:50]}'")

        assigned = sum(1 for c in result if c.role == "h2")
        print_log(f"  [TextStructureExtractor] {assigned} チャンクに role='h2' を付与しました。")
        return result

    def _find_heading_at_line_boundary(
        self, text: str, heading_pairs: list
    ) -> tuple[str, str, str] | None:
        """
        テキストを \\n で行分割し、各行頭で見出しを探す。
        確認条件: 見出しの直後の単語が大文字始まり、または行末（見出しのみ行）。
        マッチした場合 (before, heading, after) を返す。見つからなければ None。
        """
        lines = text.split("\n")
        accumulated: list[str] = []

        for i, line in enumerate(lines):
            line_words = line.split()
            if not line_words:
                accumulated.append(line)
                continue

            for norm_head, orig_head in heading_pairs:
                head_words = norm_head.split()
                # 正規化で番号が剥がれると語数が変わるため、元見出しの語数も試す。
                # 例: "1. Introduction" → norm="introduction"(1語) だが元は2語。
                n_norm = len(head_words)
                n_orig = len(orig_head.split())
                matched_n = None
                for n in dict.fromkeys([n_orig, n_norm]):  # 重複排除・順序保持
                    if n > len(line_words):
                        continue
                    candidate = " ".join(line_words[:n])
                    if self._normalize(candidate) == norm_head:
                        matched_n = n
                        break
                if matched_n is None:
                    continue
                n = matched_n

                # 見出し直後の単語が大文字始まり、または行末
                after_words = line_words[n:]
                if after_words and not after_words[0][0].isupper():
                    continue  # 小文字で続く → 文中の語 → スキップ

                # マッチ確定
                before_text = "\n".join(accumulated).strip()
                head_text   = " ".join(line_words[:n])
                after_parts = []
                if after_words:
                    after_parts.append(" ".join(after_words))
                after_parts.extend(lines[i + 1:])
                after_text = "\n".join(after_parts).strip()
                return (before_text, head_text, after_text)

            accumulated.append(line)

        return None

    @staticmethod
    def _normalize(text: str) -> str:
        """比較用に正規化（番号接頭辞除去・小文字化・記号除去）。"""
        t = re.sub(r"^[\d\.]+\s*", "", text.strip())
        t = re.sub(r"[^\w\s]", " ", t)
        return " ".join(t.lower().split())
