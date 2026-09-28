# 🌴 Agentic Blog Generator (Advanced)

Generate SEO-optimised, fact-grounded travel blogs about Goa from your own CSV datasets
(places, activities, events, food, stays, routes) using a pipeline of cooperating agents.

```
topic ─► Retrieval ─► Planner ─► Writer ─► Editor ◄─┐ (auto-revise)
            │            │          │         │─────┘
            │            │          │         ▼
            └── data ────┴──────────┴──►  SEO ─► Images ─► Exporter ─► DOCX · HTML · Markdown
```

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env            # add your Gemini key(s)

python main.py --topic "Best Waterfalls of Goa"          # full run
python main.py --topic "Top 10 Events of Goa" --limit 10
python main.py --topic "Water Sports in Goa" --type activities --no-images
python main.py --batch topics.txt                        # many blogs in one go
python main.py --topic "Historic Forts of Goa" --offline # works without any API key
python main.py --data-report                             # dataset health check
streamlit run app.py                                     # web UI
```

Each topic gets its own folder: `output/<topic-slug>/`

| File | What it is |
|---|---|
| `blog.md` | Final article (Markdown) |
| `final_blog.docx` | Word document: hero image, article index, quick-facts tables, captions, page numbers, SEO appendix |
| `blog.html` | Standalone web page (embedded images, meta tags, JSON-LD, dark mode) |
| `blog_publish.md` | Markdown with YAML front matter (WordPress / Hugo / Jekyll) |
| `seo.json` | Title, keywords, meta description, slug, alt text, SEO score, JSON-LD schema |
| `quality_report.json` | Editor findings + score |
| `records.json`, `outline.md`, `images.json`, `run.json` | Inputs, plan, image manifest, run log with timings |

## What's new compared with the first version

**Bugs fixed**
- `main.py` was an empty stub and every step needed its own `test_*.py` with hard-coded topics → one CLI + one pipeline.
- Retrieval tried to load a file that doesn't exist (`reviews_clean.csv`), matched keywords inside *any* column (a temple whose text mentioned a waterfall was returned as a waterfall; `"fort"` matched `"comfort"`) and passed 30+ empty `Unnamed:` columns to the LLM.
- The image agent and exporter built file names differently and the exporter used fuzzy matching (cutoff 0.45) → wrong photos could be attached. Now one `slugify()` + an exact `images.json` manifest.
- The writer prompt and the SEO skip-list were hard-wired to *waterfalls*; other topics produced wrong sections. Prompts are now driven by `prompts/content_types.json` (places / activities / events / food / stay).
- `raise last_error` crashed with `TypeError` when no API key was set; retry code was copy-pasted in three agents → one resilient `LLMClient`.
- SEO word count / reading time were computed by the LLM (unreliable) → computed locally.

**New functionality**
- Topic → dataset routing, subject filtering, ranking (relevance → popularity → completeness), near-duplicate removal.
- **Dataset quality guard** – detects repeated header rows, mojibake (`Goaâs`), pasted source labels, and *shifted rows* whose description belongs to another place; those rows are quarantined and listed in `output/data_quality_report.csv` so you can fix the CSV.
- Route data merged in so *How to Reach* uses real transport information.
- **Editor agent**: structure, word count, every item covered, invented sections, clichés, repeated openers, readability, FAQ size, duplicate paragraphs and **fact-grounding** (prices / distances / years must exist in the dataset). Errors trigger up to N automatic AI revisions.
- **SEO agent**: local checks + 100-point SEO score, keyword density, alt text for every image, Article + FAQPage JSON-LD.
- Images: 16:9, retries, validation, cache, hero image, placeholder fallback.
- Offline draft mode (no keys) – also what the unit tests use.
- `--resume`, `--batch`, `--words`, `--type`, `--subject`, `--formats`, per-run manifest, Streamlit UI, 25 unit tests.

## Configuration

`.env` (see `.env.example`): `GEMINI_API_KEY_1..9`, `GEMINI_MODEL`, `BLOG_REGION`, `BLOG_OUTPUT_DIR`.
Word targets scale with the number of items (≈150 words per item + 600); override with `--words 1800-2500`.
Edit tone, structure and sections in `prompts/` – no code changes needed.

## Project layout

```
main.py  pipeline.py  app.py  config.py
agents/   retrieval · planner · writer · editor · seo · image_agent · exporter
utils/    llm (key rotation + retries) · text · prompts · api_manager · logger
prompts/  writer · planner · seo · revise · image · content_types.json
data/     your CSV datasets (unchanged)
tests/    python -m unittest discover -s tests -t .
```

## Adding a new dataset / content type
1. Drop the CSV into `data/`.
2. Add a `DatasetSpec` in `agents/retrieval.py` (name column, popularity column, columns to show the LLM).
3. Add its section structure to `prompts/content_types.json`.

## Testing
```bash
python -m unittest discover -s tests -t .
```
Tests cover retrieval, data repair, text utilities, all agents offline, the complete pipeline, resume mode, and the AI path (planner → writer → editor → revise → SEO) using a fake Gemini client.

## Notes
- The default model name is kept from your original code (`gemini-3.6-flash`); set `GEMINI_MODEL` if your key uses another one.
- AI images come from Pollinations (as before). If a download fails, a placeholder card is used and a warning is printed.
- Always review the `Things to review` list printed after each run before publishing.
