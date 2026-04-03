"""
p2workflowy V2: State Integrator (Core)
複数セッションのステート（JSON）を統合し、書籍全体の TreeNode ツリーを構築する（単純積み上げ方式）。
"""

import os
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from core.models import TreeNode, load_tree_from_json

from core.config import print_log
from core.llm_client import call_gemini, get_default_model
from core.engine.p5_export.markdown_engine import MarkdownEngine
from core.engine.p5_export.workflowy_engine import WorkflowyEngine
from core.engine.p5_export.text_book_integrator import TextBookIntegrator

class StateIntegrator:
    """複数の Phase 4 (Translate) ステートを読み込み、一つの書籍ツリーに統合する（単純積み上げ方式）。"""

    def __init__(self, book_title: str, session_dir: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None):
        self.book_title = book_title
        self.session_dir = Path(session_dir) if session_dir else None
        self.api_key = api_key
        self.model = model or get_default_model("default")
        self.chapters: List[TreeNode] = []
        self.chapter_titles: List[str] = []
        self.chapter_resumes: List[str] = []

    def add_chapter(self, title: str, phase4_path: str, meta_path: Optional[str] = None, structure_path: Optional[str] = None):
        """章の内容を追加する。"""
        if not os.path.exists(phase4_path):
            print_log(f"  [Integrator] 警告: フェーズ4のパスが見つかりません: {phase4_path}")
            return

        ja_nodes = load_tree_from_json(phase4_path)
        if not ja_nodes:
            return

        # 1. メタデータからレジュメを取得
        chapter_resume = ""
        if meta_path and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta_data = json.load(f)
                    chapter_resume = meta_data.get("resume_content", "")
            except Exception:
                pass

        chapter_idx = len(self.chapters) + 1
        
        # 2. 章ルートノードの作成 (H2)
        chapter_root = TreeNode(
            id=f"ch_{chapter_idx}_root",
            text=title,
            role="h2",
            seq_index=float(chapter_idx)
        )

        # 3. [English Text] ラッパーの確保
        # Phase 4 成果物が既にラッパーを持っているか確認
        has_wrapper = any(node.text == "[English Text]" for node in ja_nodes)
        
        if not has_wrapper and structure_path and os.path.exists(structure_path):
            # ラッパーが不在で、かつ構造データ(EN)がある場合は、自律的に追加
            en_nodes = load_tree_from_json(structure_path)
            # Chapter root ノードがあればその子供を、なければノード全体を
            en_content = []
            for n in en_nodes:
                if n.role in ["h1", "h2"] and n.children:
                    en_content.extend(n.children)
                else:
                    en_content.append(n)
            
            en_wrapper = TreeNode(
                id=f"ch{chapter_idx}_en_wrapper",
                text="[English Text]",
                role="h3",
                children=en_content
            )
            chapter_root.children.append(en_wrapper)

        # 4. 子ノード（日本語本文）の移植
        for node in ja_nodes:
            # ラッパーが含まれている場合はそのまま移植（Book Mode Phase 4 成果物）
            # 含まれていない場合は日本語本文ノードとして移植（Paper Mode 成果物）
            if node.role in ["h1", "h2"] and node.children:
                for child in node.children:
                    self._apply_prefix_to_ids(child, f"ch{chapter_idx}_")
                    chapter_root.children.append(child)
            else:
                self._apply_prefix_to_ids(node, f"ch{chapter_idx}_")
                chapter_root.children.append(node)

        self.chapters.append(chapter_root)
        self.chapter_titles.append(title)
        self.chapter_resumes.append(chapter_resume)

    def _apply_prefix_to_ids(self, node: TreeNode, prefix: str):
        """ツリー内の全 ID にプレフィックスを付与して衝突を回避する。"""
        if not node.id.startswith(prefix):
            node.id = f"{prefix}{node.id}"
        for child in node.children:
            self._apply_prefix_to_ids(child, prefix)

    def _generate_consolidated_resume(self) -> str:
        """各章のレジュメから、GLOBAL_SUMMARY_PROMPT を用いて書籍全体の要約を生成する。"""
        if not self.api_key or not any(self.chapter_resumes):
            return "..."

        print_log("  [Integrator] 書籍全体のレジュメを AI で生成中 (GLOBAL_SUMMARY_PROMPT)...")
        
        from core.config import load_coreprompts
        prompts = load_coreprompts()
        base_prompt = prompts.get("GLOBAL_SUMMARY_PROMPT", "")
        
        if not base_prompt:
            print_log("  [Integrator] 警告: GLOBAL_SUMMARY_PROMPT が見つかりません。")
            return "..."

        combined_text = ""
        for i, (title, res) in enumerate(zip(self.chapter_titles, self.chapter_resumes), 1):
            combined_text += f"\n--- Chapter {i}: {title} ---\n{res}\n"
        # プロンプトのプレースホルダを埋める
        prompt = base_prompt.format(
            expertise="Anthropology / Social Science",
            context_guide=f"Book Title: {self.book_title}",
            text=combined_text
        )

        try:
            response = call_gemini(
                prompt,
                api_key=self.api_key,
                model=self.model,
                thinking_level="High"
            )
            return response.strip()
        except Exception as e:
            print_log(f"  [Integrator] 書籍レジュメ生成失敗: {e}")
            return "..."

    def integrate(self, global_resume: Optional[str] = None) -> str:
        """[Deprecated] 全章を統合し、Workflowy 形式の Markdown を返す。新エクスポートの使用を推奨。"""
        # 下位互換性のため、BookExporter を呼び出して文字列を返す
        md_engine = MarkdownEngine(base_level=2)
        wf_engine = WorkflowyEngine(base_depth=0)
        exporter = BookExporter(md_engine, wf_engine)
        return exporter.generate_workflowy(self.book_title, global_resume or "", self.chapters)

    def integrate_to_book(self, chapter_sessions: List[Dict[str, str]], global_resume: Optional[str] = None) -> List[Path]:
        """各章の生成済みテキストファイルを読み込み、TextBookIntegrator で統合する。"""
        print_log(f"  [Integrator] {len(chapter_sessions)} 章のテキストファイルを統合中 (Simple Stacking)...")
        
        # 統合対象のファイルリストを作成
        md_chapters: List[Tuple[str, Path]] = []
        wf_chapters: List[Tuple[str, Path]] = []
        
        for sess in chapter_sessions:
            title = sess["title"]
            # Chapter session から出力ファイルのパスを推定/特定
            # Phase 5 で出力される [Title]_p2.md / [Title]_p2.txt を探す
            # run_phase5 は input_path.parent に書き出すため、章PDFの隣にあるはず
            ch_path = Path(sess["state_path"]).parent # session_id ディレクトリ
            # 実際には SessionState のディレクトリ構造に依存
            from core.config import SessionState
            ch_session_id = ch_path.name
            st = SessionState(session_id=ch_session_id)
            
            # Phase 5 の出力先は BookManager では state/book_sessions/[Book]/chapters/ になる
            # かつファイル名は [Title]_p2.md
            safe_ch_title = re.sub(r'[\\/*?:"<>|]', "_", title)
            ch_dir = Path("state/book_sessions") / self.book_title / "chapters"
            
            md_file = ch_dir / f"{safe_ch_title}_p2.md"
            wf_file = ch_dir / f"{safe_ch_title}_p2.txt"
            
            if md_file.exists():
                md_chapters.append((title, md_file))
            if wf_file.exists():
                wf_chapters.append((title, wf_file))

        integrator = TextBookIntegrator()
        resume_for_export = global_resume or "..."
        
        output_paths = []
        if self.session_dir:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            
            # Markdown
            md_text = integrator.merge_markdown(self.book_title, resume_for_export, md_chapters)
            md_path = self.session_dir / f"{self.book_title}_p2.md"
            md_path.write_text(md_text, encoding="utf-8")
            output_paths.append(md_path)
            
            # Workflowy (txt)
            wf_text = integrator.merge_workflowy(self.book_title, resume_for_export, wf_chapters)
            # 3つ以上の改行を2つに圧縮 (Workflowy用クリーニング)
            wf_text = re.sub(r'\n{3,}', '\n\n', wf_text).strip() + "\n"
            txt_path = self.session_dir / f"{self.book_title}_p2.txt"
            txt_path.write_text(wf_text, encoding="utf-8")
            output_paths.append(txt_path)
            
            print_log(f"  [Integrator] 統合完了 (Text-based): {md_path.name}, {txt_path.name}")
            
        return output_paths

def run_integration_test(session_paths: List[str], meta_paths: List[str], book_title: str) -> str:
    integrator = StateIntegrator(book_title)
    for i, (p4, meta) in enumerate(zip(session_paths, meta_paths), 1):
        integrator.add_chapter(f"Chapter {i}", p4, meta)
    
    return integrator.integrate()
