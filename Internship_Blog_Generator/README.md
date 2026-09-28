# 🌴 Agentic Blog Generator (Goa)

Generates SEO-optimised, fact-grounded travel blogs about Goa from your own CSV datasets (places, activities, events, food, stays, routes) using a pipeline of cooperating agents.

```
topic ─► Retrieval ─► Planner ─► Writer ─► Editor ◄─┐ (auto-revise)
            │            │          │         │─────┘
            │            │          │         ▼
            └── data ────┴──────────┴──►  SEO ─► Images ─► Exporter ─► DOCX · HTML · Markdown
```

---

## 1. Requirements

- Python 3.10 or newer (check with `python --version`)
- Internet connection (for Gemini text and AI images)
- A free Gemini API key from https://aistudio.google.com (click **Get API key**)

---

## 2. How to run (step by step)

### Step 1 – Open the project folder
Unzip the project, then open PowerShell / Command Prompt / terminal inside the folder that contains `main.py`:

```powershell
cd Internship_Blog_Generator
```

### Step 2 – Install the packages
```powershell
pip install -r requirements.txt
```
If `pip` is not found, use `python -m pip install -r requirements.txt`.

### Step 3 – Create your `.env` file
Copy the example file and rename it to `.env`:

```powershell
copy .env.example .env        # Windows
cp .env.example .env          # Mac / Linux
```

Open `.env` and fill it in:

```
GEMINI_API_KEY_1=paste_your_real_key_here
GEMINI_MODEL=gemini-2.5-flash
```

Important:
- The file must be named exactly `.env` (not `.env.txt`) and sit next to `main.py`.
- The default model name `gemini-3.6-flash` may not exist for your key. Use a model listed in Google AI Studio, for example `gemini-2.5-flash`.
- You can add more keys (`GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`, ...). The app switches to the next key when one runs out of quota.

### Step 4 – Test without a key (optional)
This checks that everything is installed. It uses a templated draft and placeholder images:

```powershell
python main.py --topic "Historic Forts of Goa" --offline
```

### Step 5 – Run the real generator
Start with a small test:

```powershell
python main.py --topic "Best Waterfalls of Goa" --limit 3
```

Then run a full blog:

```powershell
python main.py --topic "Best Waterfalls of Goa"
```

> Always give the topic in the command with `--topic "..."`. If you run just `python main.py`, it asks for the topic and whatever you type is used as the topic.

### Step 6 – Open your results
Everything is saved inside the project folder:

```
Internship_Blog_Generator\output\<topic-name>\
```

For example `output\best-waterfalls-of-goa\`. To open it from PowerShell:

```powershell
explorer output
```

---

## 3. What happens when you run it

| # | Stage | What it does |
|---|---|---|
| 1 | **Retrieval** | Picks the right CSV(s) for the topic, filters and ranks the best items, removes duplicates, quarantines broken rows and merges route info. |
| 2 | **Planner** | Gemini creates the blog outline (`outline.md`). |
| 3 | **Writer** | Gemini writes the article using only the dataset facts. |
| 4 | **Editor** | Checks structure, length, coverage of every item, clichés, repetition and fact-grounding (prices, distances, years must exist in the data). Errors trigger up to 2 automatic AI revisions. |
| 5 | **SEO** | Creates title, keywords, meta description, slug, alt text, an SEO score and Article + FAQ JSON-LD. |
| 6 | **Images** | Generates 1 hero image plus 1 image for every place/item in the blog. |
| 7 | **Exporter** | Builds the DOCX, HTML and Markdown files. |

A summary and a "Things to review" list are printed at the end. Always read it before publishing.

### Images
- **1 hero image + 1 image per item.** A blog with 10 places gets 11 images.
- Images are AI-generated (Pollinations, Flux model, 16:9). No image key is needed, only internet.
- They are illustrative scenes based on the place type (waterfall, beach, fort ...), **not real photos of that exact place**. Mention this when publishing, or replace them with real photos.
- If a download fails after 3 tries, a green-orange placeholder card is used instead. Log lines tell you which:
  - `Generated image: ...` means an AI image was created.
  - `Placeholder image created: ...` means Pollinations failed. Run again later.
- Offline mode (`--offline`, or no valid API key) always produces placeholders.

### Output files

| File | What it is |
|---|---|
| `blog.md` | Final article (Markdown) |
| `final_blog.docx` | Word document with hero image, article index, quick-facts tables, captions, page numbers, SEO appendix |
| `blog.html` | Standalone web page (embedded images, meta tags, JSON-LD, dark mode) |
| `blog_publish.md` | Markdown with YAML front matter (WordPress / Hugo / Jekyll) |
| `seo.json` | Title, keywords, meta description, slug, alt text, SEO score, JSON-LD |
| `quality_report.json` | Editor findings and score |
| `images/` | All generated images |
| `records.json`, `outline.md`, `images.json`, `run.json` | Input data, plan, image manifest, run log with timings |

---

## 4. Useful commands

```powershell
python main.py --topic "Top 10 Events of Goa" --limit 10
python main.py --topic "Water Sports in Goa" --type activities --no-images
python main.py --topic "Best Waterfalls of Goa" --words 1800-2500
python main.py --topic "Best Waterfalls of Goa" --resume        # reuse files from a previous run
python main.py --batch topics.txt                               # many blogs, one topic per line
python main.py --topic "Historic Forts of Goa" --offline        # no API key needed
python main.py --data-report                                    # dataset health check
streamlit run app.py                                            # web interface
```

| Option | Meaning |
|---|---|
| `--topic` | Blog topic |
| `--limit N` | Number of items to feature (default 8) |
| `--type` | `places`, `activities`, `events`, `food`, `stay` (auto-detected if omitted) |
| `--subject` | Filter such as `waterfall`, `beach`, `fort` |
| `--words` | Word range, e.g. `1800-2500` |
| `--no-images` | Skip image generation |
| `--offline` | No API calls; templated draft and placeholder images |
| `--strict` | Fail instead of falling back to an offline draft |
| `--resume` | Reuse artefacts from a previous run |
| `--formats` | Choose from `docx`, `html`, `md` |

---

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| `No Gemini API key found - running in offline draft mode` | `.env` is missing, misnamed (`.env.txt`), or not next to `main.py`. Check the key line has your real key. |
| Error 404 / model not found | Change `GEMINI_MODEL` in `.env` to a model available in Google AI Studio (e.g. `gemini-2.5-flash`). |
| Error 429 / quota exceeded | Wait a while, or add more keys (`GEMINI_API_KEY_2`, ...). |
| Images are gradient placeholders | Pollinations failed or you are offline. Check internet and run again (or use `--resume`). |
| `pip` not recognised | Use `python -m pip install -r requirements.txt`. |
| It asks "Blog topic" | You ran `python main.py` without `--topic`. Press Ctrl+C and pass the topic in the command. |
| Output folder not found | Look inside the project folder: `output\<topic-name>\`. |

---

## 6. Configuration

`.env` options (see `.env.example`):

```
GEMINI_API_KEY_1..9   your Gemini keys
GEMINI_MODEL          model name
BLOG_REGION           default: Goa
BLOG_COUNTRY          default: India
BLOG_OUTPUT_DIR       default: output
LLM_MODE=offline      force offline mode
```

Word targets scale with item count (about 150 words per item + 600). Edit tone, structure and sections in the `prompts/` folder, no code changes needed.

---

## 7. Tech stack

| Area | Technology |
|---|---|
| Language | Python |
| LLM | Google Gemini (`google-genai`) with key rotation and retries |
| Data | CSV files with `pandas` |
| Images | Pollinations.ai (Flux) via `requests`; `Pillow` for validation and placeholders |
| Word export | `python-docx` |
| Web export | HTML with base64 images and JSON-LD |
| Web UI | Streamlit |
| Config | `python-dotenv` |
| Testing | `unittest` (25 tests) |

---

## 8. Project layout

```
main.py  pipeline.py  app.py  config.py
agents/   retrieval · planner · writer · editor · seo · image_agent · exporter
utils/    llm (key rotation + retries) · text · prompts · api_manager · logger
prompts/  writer · planner · seo · revise · image · content_types.json
data/     your CSV datasets
tests/    unit tests
output/   generated blogs (one folder per topic)
```

## 9. Adding a new dataset / content type
1. Drop the CSV into `data/`.
2. Add a `DatasetSpec` in `agents/retrieval.py` (name column, popularity column, columns to show the LLM).
3. Add its section structure to `prompts/content_types.json`.

## 10. Testing
```powershell
python -m unittest discover -s tests -t .
```
Covers retrieval, data repair, text utilities, all agents offline, the complete pipeline, resume mode, and the AI path using a fake Gemini client.

## 11. Notes
- Always review the "Things to review" list printed after each run before publishing.
- Fix rows listed in `output/data_quality_report.csv` to improve results.
- AI images are illustrative only; label them or replace them with real photos for publication.