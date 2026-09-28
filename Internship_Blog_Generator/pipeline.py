"""End-to-end orchestration:

    retrieve -> plan -> write -> edit (auto-revise) -> SEO -> images -> export

Every run gets its own folder (output/<topic-slug>/) with all artefacts, a
run.json manifest and per-step timings - nothing is overwritten between topics.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import config
from agents.editor import EditorAgent, QualityReport
from agents.exporter import ExporterAgent
from agents.image_agent import ImageAgent
from agents.planner import Outline, PlannerAgent
from agents.retrieval import RetrievalAgent, SPECS
from agents.seo import SEOAgent
from agents.writer import WriterAgent
from utils.llm import LLMClient
from utils.logger import get_logger
from utils.prompts import content_type_config
from utils.text import slugify

log = get_logger("pipeline")


class PipelineError(RuntimeError):
    pass


@dataclass
class RunResult:
    run_dir: Path
    files: dict[str, str] = field(default_factory=dict)
    mode: str = "offline"
    quality_score: int = 0
    seo_score: int = 0
    word_count: int = 0
    durations: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class BlogPipeline:
    def __init__(self, settings: config.Settings, llm: LLMClient | None = None,
                 retrieval: RetrievalAgent | None = None) -> None:
        self.s = settings
        self.llm = llm or LLMClient(offline=settings.offline or None)
        self.retrieval = retrieval or RetrievalAgent()
        self.planner = PlannerAgent(self.llm)
        self.writer = WriterAgent(self.llm)
        self.editor = EditorAgent()
        self.seo = SEOAgent(self.llm)
        self.images = ImageAgent(settings)
        self.warnings: list[str] = []
        self.durations: dict[str, float] = {}

    # ------------------------------------------------------------------ helpers
    def _step(self, name: str):
        log.info("▶ %s", name)
        return time.perf_counter()

    def _done(self, name: str, t0: float) -> None:
        self.durations[name] = round(time.perf_counter() - t0, 2)

    def run_dir(self) -> Path:
        path = Path(self.s.output_dir) / slugify(self.s.topic)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _write_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---------------------------------------------------------------------- run
    def run(self) -> RunResult:
        s = self.s
        run_dir = self.run_dir()
        files: dict[str, str] = {}
        if self.llm.offline and not s.offline:
            self.warnings.append("No Gemini API key found - running in offline draft mode.")
        if self.llm.offline:
            log.warning("OFFLINE MODE: the article is assembled from the dataset (no AI writing, no AI images).")
            s.offline = True

        # 1. retrieval ------------------------------------------------------
        t = self._step("Retrieving data")
        result = self.retrieval.search(s.topic, limit=s.limit, content_type=s.content_type, subject=s.subject)
        if result.empty:
            counts = {k: len(self.retrieval.load(k)) for k in SPECS}
            raise PipelineError(f"No data matched '{s.topic}' (type={result.content_type}, subject={result.subject}). "
                                f"Rows available: {counts}. Try --type / --subject or a broader topic.")
        log.info("   %s '%s' -> %d items: %s", result.content_type, result.subject or "-", len(result.records),
                 ", ".join(result.names()))
        quarantined = sum(len(q) for q in self.retrieval.quarantine.values())
        if quarantined:
            n = self.retrieval.write_quarantine_csv(Path(s.output_dir) / "data_quality_report.csv")
            self.warnings.append(f"{n} suspect dataset rows were excluded - see output/data_quality_report.csv")
        self._write_json(run_dir / "records.json", result.to_serializable())
        self._done("retrieval", t)

        # 2. plan + write (or resume) -------------------------------------------
        blog_path = run_dir / "blog.md"
        outline: Outline
        t = self._step("Planning outline")
        outline = self.planner.create_outline(s.topic, result, s)
        cfg = content_type_config(result.content_type, s.region)
        (run_dir / "outline.md").write_text(outline.to_markdown(cfg["item_structure"]), encoding="utf-8")
        self._done("plan", t)

        if s.resume and blog_path.exists():
            log.info("▶ Resuming with existing blog.md")
            blog = blog_path.read_text(encoding="utf-8")
            mode = "resumed"
        else:
            t = self._step("Writing article")
            blog = self.writer.write_blog(s.topic, outline, result, s)
            mode = self.writer.last_mode
            self._done("write", t)

        # 3. editorial loop ---------------------------------------------------------
        t = self._step("Quality check")
        draft_mode = mode == "offline"
        report = self.editor.check(blog, result, s, outline.primary_keyword, draft_mode)
        best_blog, best_report = blog, report
        revisions = 0
        while (report.errors and revisions < s.max_revisions and mode == "llm"):
            revisions += 1
            log.info("   %d issue(s) -> revision %d/%d", len(report.errors), revisions, s.max_revisions)
            blog = self.writer.revise(blog, report.feedback(), result, s)
            report = self.editor.check(blog, result, s, outline.primary_keyword, draft_mode)
            if report.score >= best_report.score:
                best_blog, best_report = blog, report
        blog, report = best_blog, best_report
        blog_path.write_text(blog, encoding="utf-8")
        self._write_json(run_dir / "quality_report.json", report.to_dict())
        log.info("   quality score %d/100 (%d errors, %d warnings)", report.score, len(report.errors),
                 sum(1 for i in report.issues if i.severity == "warning"))
        for issue in report.issues:
            if issue.severity != "info":
                self.warnings.append(f"[{issue.severity}] {issue.message}")
        self._done("quality", t)

        # 4. SEO -----------------------------------------------------------------------
        t = self._step("Generating SEO data")
        seo = self.seo.generate_seo(s.topic, blog, outline, result, s)
        self._write_json(run_dir / "seo.json", seo)
        log.info("   SEO score %d/100 | %d words | %d min read", seo["seo_score"], seo["word_count"],
                 seo["reading_time_minutes"])
        self._done("seo", t)

        # 5. images ----------------------------------------------------------------------
        if s.images:
            t = self._step("Creating images")
            manifest = self.images.generate_images(blog, result, seo, run_dir)
            fallback = sum(1 for v in manifest.values() if v["source"] == "placeholder")
            if fallback and not s.offline:
                self.warnings.append(f"{fallback} image(s) could not be generated and use placeholders.")
            self._done("images", t)

        # 6. export ------------------------------------------------------------------------
        t = self._step("Exporting")
        exporter = ExporterAgent(run_dir)
        formats = {f.strip().lower() for f in s.formats}
        if "docx" in formats and (p := exporter.export_docx()):
            files["docx"] = str(p)
        if "html" in formats and (p := exporter.export_html()):
            files["html"] = str(p)
        if "md" in formats and (p := exporter.export_markdown()):
            files["md"] = str(p)
        files.update(markdown_source=str(blog_path), seo=str(run_dir / "seo.json"),
                     quality=str(run_dir / "quality_report.json"))
        self._done("export", t)

        result_obj = RunResult(run_dir, files, mode, report.score, seo["seo_score"], seo["word_count"],
                               self.durations, self.warnings)
        self._write_json(run_dir / "run.json", {
            "topic": s.topic, "created": datetime.now().isoformat(timespec="seconds"),
            "content_type": result.content_type, "subject": result.subject, "items": result.names(),
            "mode": mode, "model": None if self.llm.offline else self.llm.model, "llm_calls": self.llm.calls,
            "revisions": revisions, "quality_score": report.score, "seo_score": seo["seo_score"],
            "word_count": seo["word_count"], "durations_sec": self.durations, "files": files,
            "warnings": self.warnings,
        })
        return result_obj
