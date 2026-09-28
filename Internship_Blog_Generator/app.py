"""Streamlit web UI:  streamlit run app.py"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

import config
from agents.retrieval import RetrievalAgent, SPECS
from pipeline import BlogPipeline, PipelineError
from utils.api_manager import APIManager

st.set_page_config(page_title="Agentic Blog Generator", page_icon="🌴", layout="wide")


@st.cache_resource
def get_retrieval() -> RetrievalAgent:
    return RetrievalAgent()


retrieval = get_retrieval()
has_keys = APIManager().has_keys

st.title("🌴 Agentic Blog Generator")
st.caption("Retrieval → Planner → Writer → Editor → SEO → Images → DOCX / HTML / Markdown")

with st.sidebar:
    st.header("Settings")
    topic = st.text_input("Blog topic", "Best Waterfalls of Goa")
    ctype = st.selectbox("Dataset", ["auto"] + sorted(SPECS))
    limit = st.slider("Items to feature", 3, 15, 8)
    offline = st.toggle("Offline draft mode (no API calls)", value=not has_keys)
    images = st.toggle("Generate images", value=True)
    formats = st.multiselect("Export formats", ["docx", "html", "md"], default=["docx", "html", "md"])
    resume = st.toggle("Resume existing draft", value=False)
    if not has_keys:
        st.warning("No GEMINI_API_KEY found in .env - offline mode only.")
    run = st.button("Generate blog", type="primary", use_container_width=True)

if run:
    settings = config.Settings(topic=topic, content_type=None if ctype == "auto" else ctype, limit=limit,
                               offline=offline, images=images, formats=tuple(formats), resume=resume)
    try:
        with st.status("Running pipeline…", expanded=True) as status:
            result = BlogPipeline(settings, retrieval=retrieval).run()
            status.update(label="Done", state="complete")
    except PipelineError as exc:
        st.error(str(exc))
        st.stop()
    st.session_state["run_dir"] = str(result.run_dir)

run_dir = Path(st.session_state["run_dir"]) if "run_dir" in st.session_state else None
if run_dir and (run_dir / "blog.md").exists():
    seo = json.loads((run_dir / "seo.json").read_text(encoding="utf-8"))
    quality = json.loads((run_dir / "quality_report.json").read_text(encoding="utf-8"))
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Words", seo["word_count"])
    c2.metric("Reading time", f"{seo['reading_time_minutes']} min")
    c3.metric("SEO score", f"{seo['seo_score']}/100")
    c4.metric("Quality score", f"{quality['score']}/100")

    tab_blog, tab_seo, tab_quality, tab_images, tab_files = st.tabs(["Article", "SEO", "Quality", "Images", "Downloads"])
    with tab_blog:
        st.markdown((run_dir / "blog.md").read_text(encoding="utf-8"))
    with tab_seo:
        st.subheader(seo["seo_title"])
        st.write(seo["meta_description"])
        st.write("**Slug:**", seo["slug"], "| **Keyword density:**", f"{seo['keyword_density_percent']}%")
        st.write("**Secondary keywords:**", ", ".join(seo["secondary_keywords"]))
        st.write("**Long-tail keywords:**", ", ".join(seo["long_tail_keywords"]))
        st.dataframe(seo["checks"], use_container_width=True)
        with st.expander("JSON-LD schema"):
            st.json(seo["json_ld"])
    with tab_quality:
        if not quality["issues"]:
            st.success("No issues found.")
        for issue in quality["issues"]:
            {"error": st.error, "warning": st.warning}.get(issue["severity"], st.info)(issue["message"])
        st.json(quality["metrics"])
    with tab_images:
        manifest = json.loads((run_dir / "images.json").read_text(encoding="utf-8")) if (run_dir / "images.json").exists() else {}
        cols = st.columns(3)
        for i, (heading, info) in enumerate(manifest.items()):
            with cols[i % 3]:
                st.image(str(run_dir / "images" / info["file"]), caption=f"{heading} ({info['source']})")
    with tab_files:
        for label, name, mime in (("Word document", "final_blog.docx",
                                   "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                                  ("HTML page", "blog.html", "text/html"),
                                  ("Markdown (with front matter)", "blog_publish.md", "text/markdown"),
                                  ("SEO JSON", "seo.json", "application/json")):
            path = run_dir / name
            if path.exists():
                st.download_button(f"Download {label}", path.read_bytes(), file_name=name, mime=mime)
else:
    st.info("Choose a topic in the sidebar and press **Generate blog**.")

with st.expander("Dataset health"):
    st.json(retrieval.data_report())
