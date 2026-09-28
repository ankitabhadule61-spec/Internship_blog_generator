"""Image agent.

* One prompt table (subject -> scene) instead of a long if-chain.
* Landscape 16:9 images (blog friendly) + a hero image for the article.
* Retries with back-off, validates every download with Pillow, caches results.
* Falls back to a clean typographic placeholder card if generation fails or
  when running offline, so the exporter always has an image.
* Writes images.json (manifest) so the exporter never has to guess file names
  with fuzzy matching (the old version could attach the wrong photo).
"""
from __future__ import annotations

import io
import json
import random
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

import config
from agents.retrieval import SearchResult, clean_value, is_blank
from utils.logger import get_logger
from utils.prompts import load_prompt, render
from utils.text import match_item, parse_blocks, slugify

log = get_logger("images")

# (keywords in name/category) -> (scene description, negative terms)
SCENES: list[tuple[tuple[str, ...], str, str]] = [
    (("waterfall", "falls", "cascade"),
     "A majestic freshwater waterfall cascading through dense Western Ghats rainforest, moss-covered rocks, mist, flowing water.",
     "sea, beach, ocean, sand, boats, buildings, city"),
    (("beach", "shore", "coastal"),
     "Golden sandy beach, turquoise Arabian Sea, coconut palms, clear sky, gentle waves.",
     "waterfall, river, snow, buildings"),
    (("temple", "mandir", "devasthan"),
     "Traditional Goan temple architecture, deepastambha lamp tower, intricate carvings, stone courtyard, lush greenery.",
     "beach, sea, waterfall, crowds"),
    (("church", "cathedral", "basilica", "chapel"),
     "Historic Portuguese church with a white facade, colonial architecture, blue sky, palm trees.",
     "beach, waterfall, crowds"),
    (("fort", "fortress"),
     "Historic laterite fort walls overlooking dramatic coastal landscape, ramparts and bastions.",
     "modern buildings, crowds"),
    (("museum", "heritage", "palace"),
     "Heritage building with Indo-Portuguese architecture, courtyard, warm daylight.",
     "crowds, modern skyline"),
    (("festival", "carnival", "event", "shigmo", "jatra"),
     "Colourful Goan festival parade with floats, traditional costumes and lights in the evening.",
     "close-up faces, text, logos"),
    (("restaurant", "cafe", "shack", "food", "seafood"),
     "Rustic Goan beach shack restaurant with a table of seafood curry, fish thali and fresh lime, warm evening light.",
     "logos, text, close-up faces"),
    (("resort", "hotel", "villa", "homestay", "stay"),
     "Boutique tropical resort with a swimming pool, palm trees and Portuguese-style villa architecture at golden hour.",
     "logos, text, crowds"),
    (("surf", "paraglid", "kite", "paddle", "water sport", "parasail", "dolphin", "kayak", "trek"),
     "Adventure travel photograph, action shot in natural light on the Goan coast or in the Western Ghats.",
     "close-up faces, logos, text"),
]
DEFAULT_SCENE = ("Natural tropical scenery typical of Goa, lush greenery, soft golden light.", "text, logos, crowds")
GLOBAL_NEGATIVE = "cartoon, anime, painting, illustration, sketch, blurry, low quality, text, logo, watermark, cgi, 3d, drawing"


def scene_for(label: str) -> tuple[str, str]:
    low = label.lower()
    for keys, scene, negative in SCENES:
        if any(k in low for k in keys):
            return scene, negative
    return DEFAULT_SCENE


class ImageAgent:
    def __init__(self, settings: config.Settings | None = None) -> None:
        self.settings = settings or config.Settings()

    # ------------------------------------------------------------------ prompts
    def create_prompt(self, place: str, category_hint: str = "") -> tuple[str, str]:
        scene, negative = scene_for(f"{place} {category_hint}")
        prompt = render(load_prompt("image_prompt.txt"), place=place, scene=scene,
                        region=self.settings.region, country=self.settings.country)
        return prompt, f"{negative}, {GLOBAL_NEGATIVE}"

    # -------------------------------------------------------------- generation
    def _download(self, prompt: str, negative: str, seed: int) -> bytes:
        s = self.settings
        url = (
            f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}"
            f"?model=flux&width={s.image_width}&height={s.image_height}&nologo=true&private=true&safe=true"
            f"&enhance=true&seed={seed}&negative_prompt={urllib.parse.quote(negative)}"
        )
        resp = requests.get(url, timeout=180)
        resp.raise_for_status()
        return resp.content

    @staticmethod
    def _valid(data: bytes, min_side: int = 256) -> bool:
        try:
            img = Image.open(io.BytesIO(data))
            img.verify()
            img = Image.open(io.BytesIO(data))
            return min(img.size) >= min_side
        except Exception:
            return False

    def placeholder(self, title: str, subtitle: str, save_path: Path) -> None:
        """Simple gradient card - keeps documents complete when AI images are unavailable."""
        w, h = self.settings.image_width, self.settings.image_height
        img = Image.new("RGB", (w, h))
        px = img.load()
        top, bottom = (14, 94, 88), (240, 168, 84)
        for y in range(h):
            t = y / (h - 1)
            colour = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            for x in range(w):
                px[x, y] = colour
        draw = ImageDraw.Draw(img)
        font_big, font_small = self._font(int(h * 0.09)), self._font(int(h * 0.045))
        words, lines, line = title.split(), [], ""
        for word in words:
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=font_big) > w * 0.8 and line:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
        y = h * 0.42 - (len(lines) * font_big.size * 0.6)
        for ln in lines[:3]:
            tw = draw.textlength(ln, font=font_big)
            draw.text(((w - tw) / 2, y), ln, fill="white", font=font_big)
            y += font_big.size * 1.2
        tw = draw.textlength(subtitle, font=font_small)
        draw.text(((w - tw) / 2, y + 10), subtitle, fill=(255, 255, 255), font=font_small)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(save_path, "PNG")

    @staticmethod
    def _font(size: int):
        for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf", "Arial.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _make_one(self, label: str, hint: str, path: Path, retries: int = 3) -> dict:
        if path.exists() and self._valid(path.read_bytes()):
            return {"file": path.name, "source": "cached"}
        if not self.settings.offline:
            prompt, negative = self.create_prompt(label, hint)
            for attempt in range(1, retries + 1):
                try:
                    data = self._download(prompt, negative, random.randint(1, 999_999))
                    if self._valid(data):
                        path.write_bytes(data)
                        log.info("Generated image: %s", path.name)
                        return {"file": path.name, "source": "ai"}
                    raise ValueError("downloaded file is not a valid image")
                except Exception as exc:  # noqa: BLE001
                    wait = 3 * attempt
                    log.warning("Image '%s' attempt %d/%d failed (%s)", label, attempt, retries, str(exc)[:70])
                    if attempt < retries:
                        time.sleep(wait)
        self.placeholder(label, f"{self.settings.region}, {self.settings.country}", path)
        log.info("Placeholder image created: %s", path.name)
        return {"file": path.name, "source": "placeholder"}

    # ----------------------------------------------------------------- pipeline API
    def generate_images(self, blog: str, result: SearchResult, seo: dict, run_dir: Path) -> dict:
        """Create hero + one image per item heading. Returns and saves the manifest."""
        image_dir = Path(run_dir) / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        names = result.names()
        blocks = parse_blocks(blog)
        title = next((t for k, t in blocks if k == "h1"), self.settings.topic)

        jobs = [("hero", title, result.content_type, image_dir / "hero.png",
                 f"{title} - {self.settings.region}, {self.settings.country}")]
        alts = seo.get("image_alt_text", {})
        for kind, heading in blocks:
            if kind != "h2":
                continue
            item = match_item(heading, names)
            if not item:
                continue
            row = result.row_for(item)
            hint = ""
            if row is not None:
                for col in ("sub_category", "category", "Category", "Sub category"):
                    if col in row and not is_blank(row[col]):
                        hint = clean_value(row[col])
                        break
            jobs.append((heading, item, hint, image_dir / f"{slugify(item, '_')}.png",
                         alts.get(heading, f"{item} in {self.settings.region}")))

        def run(job):
            heading, label, hint, path, alt = job
            info = self._make_one(label, hint, path)
            return heading, {**info, "alt": alt}

        manifest: dict[str, dict] = {}
        workers = 1 if self.settings.offline else max(1, self.settings.image_workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for heading, info in pool.map(run, jobs):
                manifest[heading] = info

        (Path(run_dir) / "images.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        ai = sum(1 for v in manifest.values() if v["source"] in {"ai", "cached"})
        log.info("Images ready: %d total (%d AI/cached, %d placeholders)", len(manifest), ai, len(manifest) - ai)
        return manifest
