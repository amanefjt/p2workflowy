"""
p2workflowy V2: State Integrator (Core)
複数セッションのステート（JSON）を統合し、書籍全体の TreeNode ツリーを構築する（単純積み上げ方式）。
"""

import re
from pathlib import Path
from typing import List, Optional, Dict, Tuple

from core.config import print_log
from core.llm_client import get_default_model
from core.engine.p5_export.text_book_integrator import TextBookIntegrator

class StateIntegrator:
    """複数の Phase 4 (Translate) ステートを読み込み、一つの書籍ツリーに統合する（単純積み上げ方式）。"""

    def __init__(self, book_title: str, session_dir: Optional[str] = None, api_key: Optional[str] = None, model: Optional[str] = None):
        self.book_title = book_title
        self.session_dir = Path(session_dir) if session_dir else None
        self.api_key = api_key
        self.model = model or get_default_model("default")

    def integrate_to_book(self, chapter_sessions: List[Dict[str, str]], global_resume: Optional[str] = None) -> List[Path]:
        """各章の生成済みテキストファイルを読み込み、TextBookIntegrator で統合する。

        chapter_sessions の各エントリは以下のいずれかの形式:
          - {"title": str, "output_paths": List[str]}  ← BookManager が output_paths を明示する場合（推奨）
          - {"title": str, "state_path": str}           ← 後方互換
        """
        print_log(f"  [Integrator] {len(chapter_sessions)} 章のテキストファイルを統合中 (Simple Stacking)...")

        md_chapters: List[Tuple[str, Path]] = []
        wf_chapters: List[Tuple[str, Path]] = []

        for sess in chapter_sessions:
            title = sess["title"]

            # --- 出力パスの解決 ---
            output_paths: List[Path] = []
            if sess.get("output_paths"):
                # 推奨パス: BookManager が run_pipeline() の戻り値を直接渡す
                output_paths = [Path(p) for p in sess["output_paths"] if p]
            else:
                print_log(f"  [Integrator] 警告: 章 '{title}' は output_paths が未設定です。統合をスキップ。")
                continue

            for p in output_paths:
                if not p.exists():
                    print_log(f"  [Integrator] ⚠️ ファイルが見つかりません: {p}")
                    continue
                if p.suffix == ".md":
                    md_chapters.append((title, p))
                elif p.suffix == ".txt":
                    wf_chapters.append((title, p))

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
