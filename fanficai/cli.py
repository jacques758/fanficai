"""Command line interface: python -m fanficai <command>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import engine, store
from .models import Character, Project
from .providers import ProviderError, get_provider


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fanficai",
        description="A fanfiction co-writing assistant: story bible in, drafts out.",
    )
    p.add_argument("--project", "-p", default=None, help="project file (default story.json)")
    p.add_argument(
        "--provider", default=None, choices=["auto", "anthropic", "openai", "ollama", "mock"]
    )
    p.add_argument("--model", default=None, help="override the model id")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="create a new story project")
    i.add_argument("--title", required=True)
    i.add_argument("--fandom", default="Original")
    i.add_argument("--pairing", default="")
    i.add_argument("--rating", default="T", choices=["G", "T", "M"])
    i.add_argument("--pov", default="third-limited")
    i.add_argument("--tense", default="past", choices=["past", "present"])
    i.add_argument("--tone", default="warm, character-driven")
    i.add_argument("--premise", default="")
    i.add_argument("--tags", type=_csv, default=[])
    i.add_argument("--avoid", type=_csv, default=[], help="tropes/content to keep out")
    i.add_argument("--force", action="store_true", help="overwrite an existing project")

    c = sub.add_parser("char", help="add or update a character")
    c.add_argument("name")
    c.add_argument("--role", default="supporting",
                   choices=["protagonist", "antagonist", "supporting"])
    c.add_argument("--traits", type=_csv, default=[])
    c.add_argument("--voice", default="")
    c.add_argument("--wants", default="")
    c.add_argument("--canon", default="", help="canon facts not to contradict")

    o = sub.add_parser("outline", help="generate a chapter outline")
    o.add_argument("--chapters", "-n", type=int, default=3)

    w = sub.add_parser("write", help="draft a chapter")
    w.add_argument("--chapter", "-c", type=int, required=True)
    w.add_argument("--words", "-w", type=int, default=800)

    k = sub.add_parser("continue", help="extend an existing chapter draft")
    k.add_argument("--chapter", "-c", type=int, required=True)
    k.add_argument("--words", "-w", type=int, default=300)
    k.add_argument("--note", default="", help="steer the continuation")

    r = sub.add_parser("critique", help="craft feedback on a chapter")
    r.add_argument("--chapter", "-c", type=int, required=True)

    l = sub.add_parser("check", help="offline continuity/craft lint (no model call)")
    l.add_argument("--chapter", "-c", type=int, required=True)

    sub.add_parser("show", help="print the story bible and progress")

    e = sub.add_parser("export", help="write the manuscript to a file")
    e.add_argument("--out", default=None)

    sub.add_parser("read", help="print a chapter").add_argument(
        "--chapter", "-c", type=int, required=True
    )
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy codepage, which mangles the curly
    # quotes and em dashes models love. Force UTF-8 where the platform allows.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass

    args = build_parser().parse_args(argv)

    try:
        return _dispatch(args)
    except store.ProjectNotFound as e:
        print(f"error: {e}", file=sys.stderr)
    except engine.SafetyRefusal as e:
        print(f"refused: {e}", file=sys.stderr)
    except ProviderError as e:
        print(f"provider error: {e}", file=sys.stderr)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
    return 1


def _dispatch(args: argparse.Namespace) -> int:
    # ---- init ------------------------------------------------------------
    if args.cmd == "init":
        target = store.path_for(args.project)
        if target.exists() and not args.force:
            print(f"error: {target} already exists (use --force)", file=sys.stderr)
            return 1
        proj = Project(
            title=args.title, fandom=args.fandom, pairing=args.pairing,
            rating=args.rating, pov=args.pov, tense=args.tense, tone=args.tone,
            premise=args.premise, tags=args.tags, avoid=args.avoid,
        )
        engine.guard(proj)
        print(f"created {store.save(proj, args.project)}")
        print("next: fanficai char \"<name>\" --role protagonist")
        return 0

    proj = store.load(args.project)

    # ---- char ------------------------------------------------------------
    if args.cmd == "char":
        existing = proj.character(args.name)
        char = existing or Character(name=args.name)
        char.role = args.role
        if args.traits:
            char.traits = args.traits
        if args.voice:
            char.voice = args.voice
        if args.wants:
            char.wants = args.wants
        if args.canon:
            char.canon_notes = args.canon
        if not existing:
            proj.characters.append(char)
        store.save(proj, args.project)
        print(("updated " if existing else "added ") + char.brief())
        return 0

    # ---- show ------------------------------------------------------------
    if args.cmd == "show":
        print(proj.bible())
        if proj.chapters:
            print("\nCHAPTERS:")
            total = 0
            for ch in sorted(proj.chapters, key=lambda c: c.number):
                mark = "drafted" if ch.text else "planned"
                total += ch.word_count
                print(f"  {ch.number:>2}. {ch.title or '(untitled)':<28} {mark:<8} {ch.word_count:>6} words")
            print(f"  {'':>2}  {'TOTAL':<28} {'':<8} {total:>6} words")
        else:
            print("\nNo chapters yet. Run: fanficai outline -n 3")
        if proj.calls:
            print(f"\nAPI SPEND: {proj.calls} calls, {proj.tokens_in} in / "
                  f"{proj.tokens_out} out tokens, ~${proj.cost_usd:.4f} estimated")
        return 0

    # ---- read ------------------------------------------------------------
    if args.cmd == "read":
        ch = proj.chapter(args.chapter)
        if ch is None:
            raise ValueError(f"no chapter {args.chapter}")
        print(f"# {ch.number}. {ch.title}\n")
        print(ch.text or "(not written yet)")
        return 0

    # ---- check (offline) -------------------------------------------------
    if args.cmd == "check":
        for note in engine.lint_chapter(proj, args.chapter):
            print(f"  - {note}")
        return 0

    # ---- export ----------------------------------------------------------
    if args.cmd == "export":
        out = Path(args.out or f"{_slug(proj.title)}.md")
        out.write_text(engine.export_markdown(proj), encoding="utf-8")
        print(f"exported {out}")
        return 0

    # ---- model-backed commands ------------------------------------------
    provider = get_provider(args.provider, args.model)
    if provider.name == "mock":
        print("[using offline mock backend - set ANTHROPIC_API_KEY or "
              "OPENAI_API_KEY for real generation]\n", file=sys.stderr)

    if args.cmd == "outline":
        proj.chapters = engine.make_outline(proj, provider, args.chapters)
        spend = engine.record_usage(proj, provider)
        store.save(proj, args.project)
        for ch in proj.chapters:
            print(f"{ch.number}. {ch.title}  [POV: {ch.pov}]")
            if ch.summary:
                print(f"    {ch.summary}")
            for b in ch.beats:
                print(f"    - {b}")
        if spend:
            print(spend, file=sys.stderr)
        return 0

    if args.cmd == "write":
        ch = engine.write_chapter(proj, provider, args.chapter, args.words)
        spend = engine.record_usage(proj, provider)
        store.save(proj, args.project)
        print(ch.text)
        print(f"\n[{ch.word_count} words -> chapter {ch.number}] {spend}", file=sys.stderr)
        return 0

    if args.cmd == "continue":
        ch = engine.continue_chapter(proj, provider, args.chapter, args.words, args.note)
        spend = engine.record_usage(proj, provider)
        store.save(proj, args.project)
        print(ch.text)
        print(f"\n[now {ch.word_count} words] {spend}", file=sys.stderr)
        return 0

    if args.cmd == "critique":
        text = engine.critique(proj, provider, args.chapter)
        spend = engine.record_usage(proj, provider)
        store.save(proj, args.project)
        print(text)
        if spend:
            print(spend, file=sys.stderr)
        return 0

    raise ValueError(f"unhandled command {args.cmd}")


def _slug(text: str) -> str:
    keep = [ch.lower() if ch.isalnum() else "-" for ch in text]
    return "".join(keep).strip("-").replace("--", "-") or "story"


if __name__ == "__main__":
    raise SystemExit(main())
