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
    page_title="p2workflowy | AI Academic Assistant",
    page_icon="📚",
    layout="wide"
)

# --- Custom CSS for Premium Look ---
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=Outfit:wght@500;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    h1, h2, h3 {
        font-family: 'Outfit', sans-serif;
        color: #1E293B;
    }

    /* Gradient Background for Sidebar */
    [data-testid="stSidebar"] {
        background-image: linear-gradient(180deg, #F8FAFC 0%, #F1F5F9 100%);
        border-right: 1px solid #E2E8F0;
    }

    /* Glassmorphism Tabs */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: #F1F5F9;
        padding: 8px;
        border-radius: 12px;
    }

    .stTabs [data-baseweb="tab"] {
        height: 45px;
        white-space: pre-wrap;
        background-color: transparent;
        border-radius: 8px;
        color: #64748B;
        font-weight: 600;
        transition: all 0.2s ease-in-out;
    }

    .stTabs [aria-selected="true"] {
        background-color: #FFFFFF !important;
        color: #4F46E5 !important;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
    }

    /* Button Styling */
    .stButton>button {
        width: 100%;
        border-radius: 12px;
        height: 3em;
        background: linear-gradient(135deg, #4F46E5 0%, #7C3AED 100%);
        color: white;
        border: none;
        font-weight: 700;
        letter-spacing: 0.5px;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }

    .stButton>button:hover {
        transform: translateY(-2px);
        box-shadow: 0 10px 15px -3px rgba(79, 70, 229, 0.3);
        background: linear-gradient(135deg, #4338CA 0%, #6D28D9 100%);
    }

    /* Result Container */
    .result-box {
        background-color: white;
        padding: 24px;
        border-radius: 16px;
        border: 1px solid #E2E8F0;
        box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1);
    }
</style>
""", unsafe_allow_html=True)

def main():
    # --- Sidebar: Settings ---
    st.sidebar.markdown("# ⚙️ Settings")
    
    # APIキー入力 (BYOK)
    api_key = st.sidebar.text_input(
        "Google API Key (Gemini)", 
        type="password", 
        placeholder="AI Studio キーを入力...",
        help="ブラウザのタブを閉じるとリセットされます。サーバーには保存されません。",
        value=st.session_state.get("api_key", "")
    )
    if api_key:
        st.session_state["api_key"] = api_key
    
    # APIキー取得のヘルプ
    if not st.session_state.get("api_key"):
        st.sidebar.info("""
        **[Gemini API キーを取得する](https://aistudio.google.com/app/apikey)**  
        (Google AI Studio で無料で取得可能です)
        """)

    model_name = st.sidebar.selectbox(
        "AI Model",
        ["gemini-2.0-flash", "gemini-2.0-flash-lite-preview-02-05", "gemini-1.5-pro", "gemini-1.5-flash"],
        index=0
    )

    st.sidebar.divider()
    st.sidebar.markdown("### 📖 Custom Glossary")
    glossary_file = st.sidebar.file_uploader("Upload glossary.csv (Optional)", type=["csv"])
    glossary_text = ""
    if glossary_file:
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

    # --- Main Area ---
    st.title("📚 p2workflowy")
    st.markdown("##### AI-Powered Academic Paper Structuring & Translation")

    # APIキーがない場合のウェルカム画面
    if not st.session_state.get("api_key"):
        st.markdown("""
        <div class="result-box">
        <h3>👋 Welcome to p2workflowy</h3>
        <p>このツールは、PDFから抽出された乱雑なテキストを、AI（Gemini）を使って美しく構造化・翻訳し、Workflowyへ直接貼り付け可能な形式に変換します。</p>
        
        <h4>利用を開始するには：</h4>
        <ol>
            <li>サイドバーの <b>Google API Key</b> 欄に Gemini の API キーを入力してください。</li>
            <li>キーが設定されると、ファイルアップローダーが表示されます。</li>
        </ol>
        <p><small>※ 入力されたキーはブラウザセッション内でのみ使用され、サーバーに保存されたり、開発者に送信されることはありません。</small></p>
        </div>
        """, unsafe_allow_html=True)
        return

    # APIキーがある場合の機能エリア
    st.write("") 
    with st.container():
        st.markdown('<div class="result-box">', unsafe_allow_html=True)
        uploaded_file = st.file_uploader("Upload Paper Text (.txt)", type=["txt"])
        st.markdown('</div>', unsafe_allow_html=True)
    
    st.write("")

    if st.button("🚀 Start Processing", disabled=not uploaded_file):
        try:
            # スキルの初期化 (ユーザーのキーを使用)
            skills = PaperProcessorSkills(api_key=st.session_state["api_key"], model_name=model_name)
            raw_text = Utils.process_uploaded_file(uploaded_file)
            
            # 各フェーズの実行
            status_container = st.status("⏳ Processing your paper...", expanded=True)
            
            async def run_pipeline():
                # Phase 1: Summarization
                status_container.write("Phase 1: Creating a semantic map (Summarization)...")
                summary_text = await skills.summarize_raw_text(raw_text)
                
                # Phase 2: Structuring
                status_container.write("Phase 2: Restoring original structure with summary hint...")
                structured_md = await skills.structure_text_with_hint(raw_text, summary_text)
                
                # Phase 3: Translation
                status_container.write("Phase 3: Parallel translation in progress...")
                translated_text = await skills.translate_academic(
                    structured_md, 
                    glossary_text=glossary_text, 
                    summary_text=summary_text,
                    progress_callback=lambda msg: status_container.write(f"Phase 3: {msg}")
                )
                
                return summary_text, translated_text
            
            # 実行
            summary_res, translated_res = asyncio.run(run_pipeline())
            status_container.update(label="✨ Processing Complete!", state="complete", expanded=False)
            
            # 結果表示
            st.divider()
            tab1, tab2, tab3 = st.tabs(["📝 Summary", "🌏 Japanese", "🔗 Combined"])
            
            with tab1:
                summary_wf = Utils.markdown_to_workflowy(summary_res)
                st.markdown("###### Copy for Workflowy")
                st.code(summary_wf, language="markdown")
                
            with tab2:
                translated_wf = Utils.markdown_to_workflowy(translated_res)
                st.markdown("###### Copy for Workflowy")
                st.code(translated_wf, language="markdown")
                
            with tab3:
                filename_stem = Path(uploaded_file.name).stem
                final_content = f"# {filename_stem}\n\n## Summary\n{summary_wf}\n\n## Japanese Translation\n{translated_wf}"
                
                st.markdown('<div class="result-box">', unsafe_allow_html=True)
                st.markdown(f"### {filename_stem}")
                st.text_area("Preview", final_content, height=400)
                
                # ダウンロードボタン
                st.download_button(
                    label="💾 Download Final Text (.txt)",
                    data=final_content,
                    file_name=f"{filename_stem}_workflowy.txt",
                    mime="text/plain"
                )
                st.markdown('</div>', unsafe_allow_html=True)
                
        except Exception as e:
            st.error(f"Error occurred: {e}")
            with st.expander("Show detailed error logs"):
                import traceback
                st.code(traceback.format_exc())

if __name__ == "__main__":
    main()
