import streamlit as st
import asyncio
import io
import re
import sys
import os
from pathlib import Path

# プロジェクトのルートディレクトリをsys.pathに追加
# src/app.py から見たプロジェクトルートは parent.parent
project_root = str(Path(__file__).parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.skills import PaperProcessorSkills
from src.utils import Utils

# ページ設定
st.set_page_config(
    page_title="p2workflowy: 論文翻訳・要約ツール",
    page_icon="📚",
    layout="wide"
)

def main():
    st.sidebar.title("🛠 設定")
    
    # APIキー入力
    api_key = st.sidebar.text_input(
        "Google API Key", 
        type="password", 
        help="Gemini APIキーを入力してください。セッション内でのみ使用されます。",
        value=st.session_state.get("api_key", "")
    )
    if api_key:
        st.session_state["api_key"] = api_key

    # モデル選択
    model_name = st.sidebar.selectbox(
        "モデル選択",
        ["gemini-2.0-flash", "gemini-2.0-flash-lite-preview-02-05", "gemini-1.5-pro", "gemini-1.5-flash"],
        index=0
    )

    # グロッサリーアップロード
    glossary_file = st.sidebar.file_uploader("辞書ファイル (glossary.csv)", type=["csv"])
    glossary_text = ""
    if glossary_file:
        # 一時ファイルに保存せずメモリで処理
        import csv
        content = glossary_file.getvalue().decode("utf-8").splitlines()
        reader = csv.reader(content)
        glossary_lines = []
        for row in reader:
            if len(row) >= 2:
                term, trans = row[0].strip(), row[1].strip()
                if term and trans:
                    glossary_lines.append(f"{term} -> {trans}")
        glossary_text = "\n".join(glossary_lines)

    st.title("📚 p2workflowy")
    st.markdown("### 論文翻訳・要約ツール (Summary-First Approach)")
    
    # メインエリア: 論文ファイルアップロード
    uploaded_file = st.file_uploader("論文テキストファイル (.txt)", type=["txt"])
    
    if st.button("🚀 処理開始", disabled=not uploaded_file or not api_key):
        if not api_key:
            st.error("APIキーを入力してください。")
            return
            
        try:
            # スキルの初期化
            skills = PaperProcessorSkills(api_key=api_key, model_name=model_name)
            raw_text = Utils.process_uploaded_file(uploaded_file)
            
            # 各フェーズの実行
            status_container = st.status("⏳ 処理を実行中...")
            
            async def run_pipeline():
                # Phase 1: Summarization
                status_container.write("Phase 1: 原文から地図（要約）を作成中...")
                summary_text = await skills.summarize_raw_text(raw_text)
                
                # Phase 2: Structuring
                status_container.write("Phase 2: 要約をヒントに構造化中...")
                structured_md = await skills.structure_text_with_hint(raw_text, summary_text)
                
                # Phase 3: Translation
                status_container.write("Phase 3: 並列翻訳を実行中...")
                translated_text = await skills.translate_academic(
                    structured_md, 
                    glossary_text=glossary_text, 
                    summary_text=summary_text,
                    progress_callback=lambda msg: status_container.write(f"Phase 3: {msg}")
                )
                
                return summary_text, translated_text
            
            # 実行
            summary_res, translated_res = asyncio.run(run_pipeline())
            status_container.update(label="✅ 処理完了！", state="complete")
            
            # 結果表示
            st.divider()
            tab1, tab2, tab3 = st.tabs(["要約 (Summary)", "翻訳 (Translation)", "結合結果 (Combined)"])
            
            with tab1:
                summary_wf = Utils.markdown_to_workflowy(summary_res)
                st.code(summary_wf, language="markdown")
                
            with tab2:
                translated_wf = Utils.markdown_to_workflowy(translated_res)
                st.code(translated_wf, language="markdown")
                
            with tab3:
                filename_stem = Path(uploaded_file.name).stem
                final_content = f"# {filename_stem}\n\n## 要約 (Summary)\n{summary_wf}\n\n## 翻訳 (Translation)\n{translated_wf}"
                st.markdown(final_content)
                
                # ダウンロードボタン
                st.download_button(
                    label="💾 結果をダウンロード (.txt)",
                    data=final_content,
                    file_name=f"{filename_stem}_output.txt",
                    mime="text/plain"
                )
                
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")
            import traceback
            st.expander("詳細なエラーログ").code(traceback.format_exc())

if __name__ == "__main__":
    main()
