"""Retrieval agent: turns a blog topic into a clean, ranked set of dataset rows.

Improvements over the first version
-----------------------------------
* No more loading of files that do not exist (reviews_clean.csv) and Events
  file names are auto-detected (Events_info.csv / Events_info_Updated.csv).
* Junk "Unnamed: N" columns, blank rows and duplicates are removed.
* Topic -> dataset routing: places / activities / events / food / stay.
* Subject filtering uses word-boundary regexes on name + category only, so a
  temple whose *description* mentions a waterfall is no longer returned for
  "waterfalls" (and "fort" no longer matches "comfort").
* Ranking by relevance -> popularity -> data completeness.
* Travel-route data is merged in so the writer can produce a real
  "How to Reach" section.
* Compact, readable context blocks for the LLM instead of DataFrame.to_string().
"""
from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field

import pandas as pd

import config
from utils.logger import get_logger
from utils.text import slugify

log = get_logger("retrieval")

# --------------------------------------------------------------------------- specs


@dataclass
class DatasetSpec:
    key: str
    files: list[str]
    name_col: str
    popularity_col: str | None
    match_cols: list[str]            # columns used for strict subject / keyword matching
    text_cols: list[str]             # weaker relevance signal
    context_cols: list[str]          # what the LLM gets to see
    facts: list[tuple[str, str]]     # (label, column) rows for the "Quick facts" table


SPECS: dict[str, DatasetSpec] = {
    "places": DatasetSpec(
        "places", ["places_info_clean.csv", "sample_places.csv"], "place_name", "Popularity Score",
        ["place_name", "category", "sub_category"], ["description", "activities"],
        ["place_name", "taluka", "category", "sub_category", "description", "historical_info", "activities",
         "entry_fee", "timings", "best_time_to_visit", "dos", "donts", "nearest_place_to_visit", "recommended_for"],
        [("Region", "taluka"), ("Entry fee", "entry_fee"), ("Timings", "timings"), ("Best time", "best_time_to_visit")],
    ),
    "activities": DatasetSpec(
        "activities", ["activities_info.csv"], "activity_name", "popularity_score",
        ["activity_name", "category", "sub_category"], ["description", "highlights"],
        ["activity_name", "category", "description", "suitable_for", "minimum_age", "difficulty_level", "duration",
         "location", "village", "average_cost", "currency", "booking_required", "best_season", "what_to_carry",
         "restrictions", "highlights"],
        [("Location", "location"), ("Duration", "duration"), ("Difficulty", "difficulty_level"),
         ("Average cost", "average_cost"), ("Best season", "best_season")],
    ),
    "events": DatasetSpec(
        "events", ["Events_info.csv", "Events_info_Updated.csv"], "event_name", "Popularity Score",
        ["event_name", "category", "sub_category"], ["description"],
        ["event_name", "category", "description", "venue", "village", "start_date", "end_date", "recurrence",
         "ticket_required", "ticket_price", "currency", "best_time_to_visit", "nearby_attractions", "safety_tips",
         "expected_crowd"],
        [("Venue", "venue"), ("When", "start_date"), ("Recurrence", "recurrence"), ("Ticket price", "ticket_price")],
    ),
    "food": DatasetSpec(
        "food", ["food_info.csv"], "Name", "popularity score",
        ["Name", "Category", "Sub category", "Cuisine types"], ["Description", "Popular dishes"],
        ["Name", "Category", "Sub category", "Description", "Cuisine types", "Restaurant type", "User rating",
         "Popular dishes", "Price range (for two)", "Currency", "Village", "Nearest beach", "Nearest landmark",
         "Opening time", "Closing time", "Veg / Non Veg / Pure Veg"],
        [("Cuisine", "Cuisine types"), ("Price for two", "Price range (for two)"), ("Village", "Village"),
         ("Type", "Veg / Non Veg / Pure Veg")],
    ),
    "stay": DatasetSpec(
        "stay", ["stay_info.csv"], "place_name", "popularity between 0-10",
        ["place_name", "category", "sub_category"], ["description", "best_for"],
        ["place_name", "category", "sub_category", "description", "star_rating", "user_rating", "price_per_night",
         "currency", "village", "nearest_beach", "amenities", "family_friendly", "best_for", "nearby_attractions"],
        [("Price per night", "price_per_night"), ("Star rating", "star_rating"), ("Village", "village"),
         ("Best for", "best_for")],
    ),
}

ROUTES_FILE = "travel_route_clean.csv"

# regex per subject (matched on name + category + sub_category only)
SUBJECT_PATTERNS: dict[str, str] = {
    "waterfall": r"\b(waterfalls?|falls|cascades?)\b",
    "beach": r"\bbeach(es)?\b",
    "fort": r"\b(forts?|fortress|fortaleza)\b",
    "temple": r"\b(temples?|mandir|devasthan)\b",
    "church": r"\b(church(es)?|chapel|cathedral|basilica)\b",
    "mosque": r"\b(mosques?|masjid)\b",
    "museum": r"\bmuseums?\b",
    "viewpoint": r"\b(view ?points?|sunset points?|scenic)\b",
    "lake": r"\b(lakes?|rivers?|creeks?)\b",
    "wildlife": r"\b(wildlife|sanctuary|national park|bird)\b",
    "heritage": r"\b(heritage|archaeological|monuments?|palace)\b",
    "cave": r"\bcaves?\b",
    "island": r"\bisland\b",
    "market": r"\b(markets?|bazaar|flea)\b",
}

TYPE_HINTS: list[tuple[str, str]] = [
    ("events", r"\b(events?|festivals?|carnival|fest|celebrations?)\b"),
    ("stay", r"\b(stay|hotels?|resorts?|hostels?|homestays?|villas?|accommodation)\b"),
    ("food", r"\b(food|restaurants?|cafes?|eat|eating|cuisine|seafood|dining|shacks?)\b"),
    ("activities", r"\b(activit(?:y|ies)|adventure|water sports?|things to do|trekking|sports?)\b"),
]

STOPWORDS = {
    "top", "best", "the", "a", "an", "of", "in", "to", "for", "and", "on", "at", "with", "goa", "must", "visit",
    "guide", "ultimate", "complete", "things", "do", "popular", "famous", "places", "place", "events", "event",
    "activities", "activity", "restaurants", "restaurant", "food", "stay", "hotels", "hotel", "amazing", "hidden",
    "gem", "gems", "your", "you", "india", "north", "south", "trip", "travel", "2024", "2025", "2026",
}

NULL_STRINGS = {"", "nan", "none", "null", "n/a", "na", "-", "not available", "nil", "tbd", "unknown",
                "not specified", "nearby village/attractions", "not applicable"}


def is_blank(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in NULL_STRINGS


def fix_text(value):
    """Repair copy-paste damage found in the CSVs: mojibake, literal '\\n', stray source citations."""
    if not isinstance(value, str):
        return value
    text = value
    for bad, good in (("â\x80\x99", "'"), ("â\x80\x9c", '"'), ("â\x80\x9d", '"'), ("â\x80\x93", "–"),
                      ("â\x80\x94", "—")):
        text = text.replace(bad, good)
    text = re.sub(r"(?<=[A-Za-z])â(?=[a-z])", "'", text)      # Goaâs -> Goa's
    text = re.sub(r"\sâ\s", " – ", text)                       # broken dashes
    parts = re.split(r"\\n|\n", text)
    kept = []
    for part in parts:
        seg = part.strip()
        if not seg:
            continue
        # a short fragment right after a replacement character is a pasted source label ("Goa Tourism")
        if kept and re.search(r"(ï¿½|�)\s*$", kept[-1]) and len(seg.split()) <= 6 and not re.search(r"[.!?]$", seg):
            continue
        kept.append(seg)
    text = " ".join(kept)
    text = text.replace("ï¿½", "").replace("�", "")
    return re.sub(r"\s{2,}", " ", text).strip()


def clean_value(value, max_chars: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if max_chars and len(text) > max_chars:
        text = text[: max_chars - 1].rsplit(" ", 1)[0] + "…"
    return text


GENERIC_TOKENS = {
    "waterfall", "waterfalls", "falls", "fall", "beach", "temple", "mandir", "devasthan", "saunsthan", "shri",
    "shree", "sri", "church", "chapel", "fort", "mahadev", "also", "known", "the", "of", "and", "goa", "north",
    "south", "view", "point", "viewpoint", "museum", "lake", "river", "market", "festival", "resort", "hotel",
    "restaurant", "cafe", "shack", "with", "near", "area", "another", "seasonal", "scenic", "popular", "this",
}


def _sig_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", str(text).lower())
    return {w for w in words if len(w) >= 4 and w not in GENERIC_TOKENS}


def description_lead(desc: str) -> str | None:
    """Subject a description starts with ('Pali Waterfall, also known as...' -> 'Pali Waterfall')."""
    text = str(desc).strip()
    if not text or not text[0].isupper() or text.startswith(("A ", "An ", "The ", "This ", "Located ", "Situated ")):
        return None
    head = re.split(r"\s(?:is|are|was|lies|sits|stands|offers|features)\s|,|\(|\.", text[:90], maxsplit=1)[0].strip()
    words = head.split()
    if len(words) < 2 or words[0].lower() in GENERIC_TOKENS:      # "Casual, ..." / "Restaurant in X" are not names
        return None
    connectors = {"of", "the", "and", "de", "da", "do", "e", "at", "in", "-", "&"}
    proper = all(w[0].isupper() or w.lower() in connectors or not w[0].isalpha() for w in words)
    return head if 1 <= len(words) <= 6 and proper else None


def is_misaligned(name: str, desc: str) -> bool:
    """True when a description clearly talks about a *different* place than its row name.

    The places CSV contains shifted rows (e.g. 'Kesarval Waterfall' carrying the text of 'Pali Waterfall'),
    which would make the blog publish wrong facts.
    """
    lead = description_lead(desc)
    if not lead:
        return False
    lead_tokens, name_tokens = _sig_tokens(lead), _sig_tokens(name)
    if not lead_tokens or not name_tokens:
        return False
    opening = str(desc)[:300].lower()
    if any(tok in opening for tok in name_tokens):               # row name appears in the opening text
        return False
    for a in lead_tokens:
        for b in name_tokens:
            if a[:5] == b[:5]:
                return False
    return True


# --------------------------------------------------------------------------- result object


@dataclass
class SearchResult:
    content_type: str
    subject: str | None
    topic: str
    records: pd.DataFrame
    spec: DatasetSpec
    routes: dict[str, str] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.records.empty

    def names(self) -> list[str]:
        return self.records[self.spec.name_col].astype(str).tolist() if not self.empty else []

    def row_for(self, name: str) -> pd.Series | None:
        target = slugify(name)
        for _, row in self.records.iterrows():
            if slugify(row[self.spec.name_col]) == target:
                return row
        close = difflib.get_close_matches(target, [slugify(n) for n in self.names()], n=1, cutoff=0.8)
        if close:
            for _, row in self.records.iterrows():
                if slugify(row[self.spec.name_col]) == close[0]:
                    return row
        return None

    def facts_for(self, name: str) -> list[tuple[str, str]]:
        row = self.row_for(name)
        if row is None:
            return []
        facts = []
        for label, col in self.spec.facts:
            if col in row and not is_blank(row[col]):
                value = clean_value(row[col], 220)
                if label == "When" and "end_date" in row and not is_blank(row["end_date"]) and row["end_date"] != row[col]:
                    value = f"{value} – {clean_value(row['end_date'], 40)}"
                if label == "Average cost" and "currency" in row and not is_blank(row["currency"]):
                    value = f"{value} {row['currency']}"
                facts.append((label, value))
        return facts

    def route_for(self, name: str) -> str | None:
        return self.routes.get(slugify(name))

    def to_context(self, max_chars: int = 380) -> str:
        """Readable record blocks for the LLM (skips empty fields)."""
        blocks = []
        for i, (_, row) in enumerate(self.records.iterrows(), start=1):
            name = clean_value(row[self.spec.name_col])
            lines = [f"[{i}] {name}"]
            for col in self.spec.context_cols:
                if col == self.spec.name_col or col not in row or is_blank(row[col]):
                    continue
                lines.append(f"  - {col}: {clean_value(row[col], max_chars)}")
            route = self.route_for(name)
            if route:
                lines.append(f"  - ROUTE: {route}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def to_serializable(self) -> list[dict]:
        out = []
        for name in self.names():
            out.append({"name": name, "facts": self.facts_for(name), "route": self.route_for(name)})
        return out


# --------------------------------------------------------------------------- agent


class RetrievalAgent:
    def __init__(self, data_folder: str | os.PathLike | None = None) -> None:
        self.data_folder = str(data_folder or config.DATA_DIR)
        self._cache: dict[str, pd.DataFrame] = {}
        self._routes: dict[str, str] | None = None
        self.quarantine: dict[str, pd.DataFrame] = {}

    # ---- loading ---------------------------------------------------------
    def _find_file(self, candidates: list[str]) -> str | None:
        if not os.path.isdir(self.data_folder):
            return None
        existing = {f.lower(): f for f in os.listdir(self.data_folder)}
        for cand in candidates:
            if cand.lower() in existing:
                return os.path.join(self.data_folder, existing[cand.lower()])
        for cand in candidates:  # renamed copies like "Events_info (1).csv"
            base = cand.lower().replace(".csv", "")
            for low, real in existing.items():
                if low.startswith(base) and low.endswith(".csv"):
                    return os.path.join(self.data_folder, real)
        return None

    @staticmethod
    def _read_csv(path: str) -> pd.DataFrame:
        for enc in ("utf-8", "utf-8-sig", "cp1252", "latin1"):
            try:
                return pd.read_csv(path, encoding=enc)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Could not decode {path}")

    def load(self, content_type: str) -> pd.DataFrame:
        if content_type in self._cache:
            return self._cache[content_type]
        spec = SPECS[content_type]
        path = self._find_file(spec.files)
        if not path:
            log.warning("No data file found for '%s' (looked for %s)", content_type, spec.files)
            self._cache[content_type] = pd.DataFrame()
            return self._cache[content_type]
        df = self._read_csv(path)
        df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all")
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].map(fix_text)
        if spec.name_col not in df.columns:
            log.warning("Column '%s' missing in %s", spec.name_col, os.path.basename(path))
            self._cache[content_type] = pd.DataFrame()
            return self._cache[content_type]
        df = df[~df[spec.name_col].map(is_blank)].copy()
        df[spec.name_col] = df[spec.name_col].map(lambda v: clean_value(v))
        # drop header rows that were pasted into the data (e.g. a row whose name is literally "Place Name")
        header_forms = {re.sub(r"[^a-z]", "", spec.name_col.lower()), "name", "placename", "eventname", "activityname"}
        df = df[~df[spec.name_col].map(
            lambda v: re.sub(r"[^a-z]", "", v.lower()) in header_forms or bool(re.fullmatch(r"[A-Za-z /]*\bname\b[A-Za-z /]*", v, re.I))
        )].copy()
        pop_col = spec.popularity_col if spec.popularity_col in df.columns else None
        df["_popularity"] = pd.to_numeric(df[pop_col], errors="coerce") if pop_col else float("nan")
        cols = [c for c in spec.context_cols if c in df.columns]
        df["_completeness"] = df[cols].map(lambda v: 0 if is_blank(v) else 1).sum(axis=1) if cols else 0
        df["_slug"] = df[spec.name_col].map(slugify)
        desc_col = "description" if "description" in df.columns else ("Description" if "Description" in df.columns else None)
        if desc_col:
            df["_aligned"] = [not is_misaligned(n, d) for n, d in zip(df[spec.name_col], df[desc_col].fillna(""))]
            df["_desc_len"] = df[desc_col].fillna("").astype(str).str.len().clip(upper=400)
        else:
            df["_aligned"], df["_desc_len"] = True, 0
        # among duplicates keep the aligned, richest row
        df = df.sort_values(["_aligned", "_desc_len", "_completeness", "_popularity"], ascending=False)
        df = df.drop_duplicates("_slug")
        bad = df[~df["_aligned"]]
        if not bad.empty:
            self.quarantine[content_type] = bad.copy()
            log.warning("%s: quarantined %d rows whose description belongs to a different place "
                        "(see --data-report)", content_type, len(bad))
            df = df[df["_aligned"]]
        self._cache[content_type] = df.reset_index(drop=True)
        log.info("Loaded %-10s %4d usable rows from %s", content_type, len(df), os.path.basename(path))
        return self._cache[content_type]

    def load_routes(self) -> dict[str, str]:
        if self._routes is not None:
            return self._routes
        path = self._find_file([ROUTES_FILE])
        routes: dict[str, str] = {}
        if path:
            df = self._read_csv(path)
            df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
            for _, row in df.iterrows():
                if is_blank(row.get("place_name")):
                    continue
                key = slugify(row["place_name"])
                if key in routes:
                    continue
                parts = []
                if not is_blank(row.get("by_road")):
                    parts.append(f"By road: {clean_value(row['by_road'], 260)}")
                if not is_blank(row.get("nearest_railhead")):
                    parts.append(f"Nearest railhead: {clean_value(row['nearest_railhead'], 200)}")
                if not is_blank(row.get("local_transport_options")):
                    parts.append(f"Local transport: {clean_value(row['local_transport_options'], 200)}")
                if parts:
                    routes[key] = " | ".join(parts)
        self._routes = routes
        return routes

    # ---- topic understanding --------------------------------------------
    @staticmethod
    def detect_content_type(topic: str) -> str:
        low = topic.lower()
        for ctype, pattern in TYPE_HINTS:
            if re.search(pattern, low):
                return ctype
        return "places"

    @staticmethod
    def detect_subject(topic: str) -> str | None:
        low = topic.lower()
        for subject, pattern in SUBJECT_PATTERNS.items():
            if re.search(pattern, low):
                return subject
        return None

    @staticmethod
    def topic_tokens(topic: str) -> list[str]:
        words = re.findall(r"[a-z]+", topic.lower())
        tokens = []
        for w in words:
            if w in STOPWORDS or len(w) < 3:
                continue
            tokens.append(w[:-1] if w.endswith("s") and len(w) > 4 else w)
        return tokens

    # ---- helpers ------------------------------------------------------------
    @staticmethod
    def _core_name(name: str) -> str:
        """'Dudhsagar Waterfall (Mollem)' -> 'dudhsagar' so near-duplicates can be detected."""
        name = re.sub(r"\(.*?\)", " ", str(name).lower())
        generic = r"\b(waterfalls?|falls?|beach|temple|church|fort|the|shri|shree|sri|mahadev|of|and|view|point)\b"
        name = re.sub(generic, " ", name)
        return re.sub(r"[^a-z0-9]+", " ", name).strip()

    def _drop_near_duplicates(self, df: pd.DataFrame, name_col: str) -> pd.DataFrame:
        kept_idx, kept_cores = [], []
        for idx, name in df[name_col].items():
            core = self._core_name(name) or slugify(name)
            if any(core == c or difflib.SequenceMatcher(None, core, c).ratio() > 0.9 for c in kept_cores):
                continue
            kept_idx.append(idx)
            kept_cores.append(core)
        return df.loc[kept_idx]

    # ---- search -----------------------------------------------------------
    def search(self, topic: str, limit: int = 8, content_type: str | None = None,
               subject: str | None = None) -> SearchResult:
        ctype = content_type or self.detect_content_type(topic)
        if ctype not in SPECS:
            raise ValueError(f"Unknown content type '{ctype}'. Choose from {sorted(SPECS)}")
        spec = SPECS[ctype]
        df = self.load(ctype)
        subject = subject or (self.detect_subject(topic) if ctype == "places" else None)
        if df.empty:
            return SearchResult(ctype, subject, topic, pd.DataFrame(), spec)

        df = df.copy()
        df["_score"] = 0.0
        mask = pd.Series(True, index=df.index)

        if subject and subject in SUBJECT_PATTERNS:
            pattern = re.compile(SUBJECT_PATTERNS[subject], re.IGNORECASE)
            hay = df[[c for c in spec.match_cols if c in df.columns]].fillna("").astype(str).agg(" ".join, axis=1)
            mask = hay.map(lambda s: bool(pattern.search(s)))
            in_name = df[spec.name_col].astype(str).map(lambda s: bool(pattern.search(s)))
            # a name match ("Fort Aguada") outranks a category-only match
            df["_score"] = mask.astype(float) + in_name.astype(float)
        else:
            tokens = self.topic_tokens(topic)
            if tokens:
                hay_match = df[[c for c in spec.match_cols if c in df.columns]].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
                hay_text = df[[c for c in spec.text_cols if c in df.columns]].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
                score = pd.Series(0.0, index=df.index)
                for tok in tokens:
                    rx = re.compile(rf"\b{re.escape(tok)}")
                    score += hay_match.map(lambda s: 2.0 if rx.search(s) else 0.0)
                    score += hay_text.map(lambda s: 0.5 if rx.search(s) else 0.0)
                df["_score"] = score
                mask = score > 0

        result = df[mask]
        if result.empty and subject is None and self.topic_tokens(topic):
            log.warning("No rows matched topic keywords; falling back to most popular %s", ctype)
            result = df
        result = result.sort_values(["_score", "_popularity", "_completeness"], ascending=False)
        result = self._drop_near_duplicates(result, spec.name_col).head(limit).reset_index(drop=True)

        routes = {}
        if ctype == "places" and not result.empty:
            all_routes = self.load_routes()
            keys = list(all_routes)
            for name in result[spec.name_col]:
                slug = slugify(name)
                if slug in all_routes:
                    routes[slug] = all_routes[slug]
                    continue
                close = difflib.get_close_matches(slug, keys, n=1, cutoff=0.85)
                if close:
                    routes[slug] = all_routes[close[0]]
        return SearchResult(ctype, subject, topic, result, spec, routes)

    # ---- backwards compatible helpers (old API) ---------------------------
    def search_places(self, keyword: str) -> pd.DataFrame:
        return self.search(f"{keyword}", limit=50, content_type="places",
                           subject=keyword.lower() if keyword.lower() in SUBJECT_PATTERNS else None).records

    def search_events(self, limit: int = 10) -> pd.DataFrame:
        return self.search("events", limit=limit, content_type="events").records

    def search_activities(self, limit: int = 10) -> pd.DataFrame:
        return self.search("activities", limit=limit, content_type="activities").records

    # ---- data quality ------------------------------------------------------
    def data_report(self) -> dict:
        report = {}
        for ctype, spec in SPECS.items():
            df = self.load(ctype)
            if df.empty:
                report[ctype] = {"rows": 0}
                continue
            core = [c for c in spec.context_cols if c in df.columns]
            fill = {c: round(100 * (1 - df[c].map(is_blank).mean())) for c in core}
            report[ctype] = {
                "rows": len(df),
                "quarantined_rows": int(len(self.quarantine.get(ctype, []))),
                "with_popularity": int(df["_popularity"].notna().sum()),
                "fill_percent": fill,
            }
        report["routes"] = {"rows": len(self.load_routes())}
        return report

    def write_quarantine_csv(self, path) -> int:
        """Save every suspect row so the dataset owner can fix the source CSV."""
        frames = []
        for ctype in SPECS:
            self.load(ctype)
            q = self.quarantine.get(ctype)
            if q is not None and not q.empty:
                spec = SPECS[ctype]
                desc_col = "description" if "description" in q.columns else "Description"
                frames.append(pd.DataFrame({
                    "dataset": ctype, "name": q[spec.name_col],
                    "description_starts_with": q[desc_col].astype(str).str[:120],
                    "problem": "Description does not match this row (shifted rows or misplaced column?)",
                }))
        if not frames:
            return 0
        out = pd.concat(frames, ignore_index=True)
        out.to_csv(path, index=False, encoding="utf-8-sig")
        return len(out)
