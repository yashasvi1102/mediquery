import streamlit as st
from pathlib import Path

st.set_page_config(page_title="MediQuery", page_icon="🏥", layout="wide")

st.markdown("""
<style>
#MainMenu, footer, .stDeployButton, header[data-testid="stHeader"] { display: none !important; }
.block-container { padding: 0 !important; max-width: 100% !important; }
iframe { border: none !important; }
</style>
""", unsafe_allow_html=True)

html_content = Path("index.html").read_text(encoding="utf-8")
st.components.v1.html(html_content, height=1000, scrolling=True)