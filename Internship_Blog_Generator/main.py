#!/usr/bin/env python3
"""Agentic Blog Generator - command line interface.

Examples
--------
  python main.py --topic "Best Waterfalls of Goa"
  python main.py --topic "Top 10 Events of Goa" --limit 10 --formats docx,html
  python main.py --topic "Water Sports in Goa" --type activities --no-images
  python main.py --batch topics.txt --resume
  python main.py --topic "Historic Forts of Goa" --offline      # no API keys needed
  python main.py --data-report                                   # dataset health check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from agents.retrieval import RetrievalAgent, SPECS
from pipeline import BlogPipeline, PipelineError
from utils.logger import get_logger

log = get_logger("main")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Generate SEO-optimised travel blogs from your Goa datasets.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--topic", help='Blog topic, e.g. "Best Waterfalls of Goa"')
    p.add_argument("--batch", metavar="FILE", help="Text file with one topic per line (# for comments)")
    p.add_argument("--type", choices=sorted(SPECS), help="Dataset to use (auto-detected from the topic)")
    p.add_argument("--subject", help="Force a place subject: waterfall, beach, fort, temple, church ...")
    p.add_argument("--limit", type=int, default=8, help="How many items to feature (default 8)")
    p.add_argument("--words", help="Word range, e.g. 1800-2500 (default scales with item count)")
    p.add_argument("--formats", default="docx,html,md", help="Comma list of: docx,html,md")
    p.add_argument("--no-images", action="store_true", help="Skip image generation")
    p.add_argument("--offline", action="store_true", help="No API calls: data-driven draft + placeholder images")
    p.add_argument("--strict", action="store_true", help="Fail instead of falling back to offline drafts")
    p.add_argument("--revisions", type=int, default=2, help="Max AI revision passes (default 2)")
    p.add_argument("--resume", action="store_true", help="Reuse an existing blog.md for this topic")
    p.add_argument("--output", help="Output folder (default ./output)")
    p.add_argument("--region", default=config.REGION, help="Region name used in prompts (default Goa)")
    p.add_argument("--data-report", action="store_true", help="Print dataset health and exit")
    return p


def settings_from(args, topic: str) -> config.Settings:
    s = config.Settings(topic=topic, content_type=args.type, subject=args.subject, limit=args.limit,
                        offline=args.offline, strict=args.strict, images=not args.no_images,
                        formats=tuple(args.formats.split(",")), max_revisions=args.revisions,
                        resume=args.resume, region=args.region)
    if args.output:
        s.output_dir = Path(args.output)
    if args.words:
        lo, _, hi = args.words.partition("-")
        s.min_words, s.max_words = int(lo), int(hi or int(lo) + 700)
    return s


def show_data_report() -> None:
    agent = RetrievalAgent()
    report = agent.data_report()
    print(json.dumps(report, indent=2))
    out = config.OUTPUT_DIR / "data_quality_report.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    n = agent.write_quarantine_csv(out)
    print(f"\n{n} suspect rows written to {out}" if n else "\nNo suspect rows found.")


def summarise(topic: str, res) -> None:
    print("\n" + "=" * 64)
    print(f" DONE: {topic}")
    print("=" * 64)
    print(f" Mode            : {res.mode}")
    print(f" Words           : {res.word_count}")
    print(f" Quality score   : {res.quality_score}/100")
    print(f" SEO score       : {res.seo_score}/100")
    print(f" Output folder   : {res.run_dir}")
    for kind, path in res.files.items():
        print(f"   - {kind:<16}{Path(path).name}")
    if res.warnings:
        print("\n Things to review:")
        for w in res.warnings[:8]:
            print(f"   ! {w}")
    print("=" * 64)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.data_report:
        show_data_report()
        return 0

    topics: list[str] = []
    if args.batch:
        lines = Path(args.batch).read_text(encoding="utf-8").splitlines()
        topics = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    elif args.topic:
        topics = [args.topic]
    else:
        typed = input("Blog topic (e.g. Best Waterfalls of Goa): ").strip()
        if not typed:
            print("No topic given.")
            return 1
        topics = [typed]

    failures = 0
    retrieval = RetrievalAgent()                  # datasets are loaded once and reused
    for topic in topics:
        try:
            res = BlogPipeline(settings_from(args, topic), retrieval=retrieval).run()
            summarise(topic, res)
        except PipelineError as exc:
            failures += 1
            log.error("%s", exc)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            log.exception("Topic '%s' failed: %s", topic, exc)
    return 1 if failures == len(topics) else 0


if __name__ == "__main__":
    sys.exit(main())
