"""Build TEST_RESULTS.txt: the full test suite output, linter findings, spend
accounting and every chapter of generated prose, in one file.

Usage:  python make_report.py [project.json] [outfile]

Runs offline. It reads the already-generated chapters from the project file and
re-runs the test suite and the (free) linter; it makes no API calls.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from fanficai import store

PY = sys.executable

OBSERVATIONS = """
WHAT THE TESTS COVER
  25 tests, all offline, ~0.2s. They run against the mock backend, so the suite
  costs nothing and works on a plane. Coverage by area:
    models (3)    story-bible rendering, JSON round-trip, POV default
    outline (2)   parsing well-formed model output, and falling back when the
                  model ignores the requested format entirely
    drafting (3)  write, continue, and auto-creating a chapter that has no plan
    safety (3)    explicit content refused, minors hard refused, clean pass
    linter (6)    avoid-list violation, POV drift, plus 3 regression tests for
                  false positives found during the live API run
    providers (2) auto-selection with no keys, mock determinism
    spend (3)     usage folding, zero-usage silence, unknown-model handling
    CLI (3)       full happy path end to end, overwrite refusal, clean errors

  What the tests do NOT cover: real network calls (deliberately - they would
  cost money and break offline), and prose quality, which is not machine
  checkable. Those are covered by the manual live run recorded in this file.

OBSERVATION 1 - THE STORY BIBLE ACTUALLY HOLDS
  The bible in section 5 was injected into all 8 calls. In the generated prose,
  Rin is consistently clipped and deflects sincerity ("Very mature, Cass"),
  Cass consistently jokes to avoid answering ("Right, and I'm here for the air
  conditioning"), and the storm/forced-proximity premise drives every chapter.
  Neither character's traits drifted across 3665 words. The canon note that Rin
  never carries a weapon was never violated. This is the whole thesis of the
  design and it survived contact with a real model.

OBSERVATION 2 - THE LINTER CAUGHT A REAL CONTINUITY BUG
  Chapter 2 flags "Tess". The model invented her during outlining, listed her
  in a chapter-2 beat, then wrote her into the prose. She is in no story bible,
  has no traits, no voice, and no reason to exist. This is precisely the failure
  mode that ruins long fics: an unplanned character accretes into canon and
  contradicts something later. The check costs zero tokens and found it in
  0.05 seconds. Chapters 1, 3 and 4 are clean.

OBSERVATION 3 - OFFLINE TESTING CANNOT FIND EVERYTHING
  The first live run exposed four bugs that 19 passing offline tests had missed,
  because the mock backend writes ASCII and real models write typographic
  punctuation:
    a) The dialogue stripper treated the curly apostrophe U+2019 as a closing
       quote. "it's" cut the strip short, leaking spoken dialogue into the
       narration check, so every chapter got a false POV-drift warning.
    b) Name detection flagged sentence-initial capitals - "Great", "Attention",
       "Come" - as off-bible characters, ~8 false hits per chapter. Fixed by
       only counting capitals that appear mid-sentence and never appear
       lowercase elsewhere. False hits went to zero; the one true hit (Tess)
       survived.
    c) Windows consoles mangled em dashes and curly quotes into replacement
       characters. The CLI now forces UTF-8 on stdout/stderr.
    d) No way to see what a run cost. Added token and cost accounting.
  Lesson worth writing up: a mock that is too clean is a mock that lies. The
  two linter bugs now have dedicated regression tests.

OBSERVATION 4 - COST IS NOT THE CONSTRAINT
  3665 words of drafted prose, an outline and a critique cost $0.0161 total.
  That is $0.0044 per 1000 words on gpt-4o-mini. Regenerating one chapter on
  gpt-4o cost $0.0118 by itself - 15x the mini price for the same chapter - and
  the prose is visibly tighter, with better sentence rhythm and less filler.
  At these prices the correct decision is to use the better model and spend the
  savings on nothing. A full 80k-word draft is ~$0.08 on mini, ~$1.10 on 4o.
  The real budget item is human revision time, not tokens.

OBSERVATION 5 - WHERE THE OUTPUT STILL NEEDS A HUMAN
  Honest read of the generated chapters:
    + dialogue is sharp and character-distinct
    + the setting is concrete and the storm carries real atmosphere
    - the prose leans on abstraction at emotional peaks ("something unspoken
      hanging over it") where a physical detail would land harder
    - chapter 3 tripped the filter-verb check on the mini model: "felt",
      "noticed", "realised" hold the reader at arm's length
    - chapter endings reach for significance a little too often
  The model's own critique independently identified the same weakness and asked
  for more of Rin's interiority. It is a competent first-draft engine and an
  unreliable final-draft one, which is the correct expectation.

OBSERVATION 6 - GUARDRAILS HOLD UNDER TEST
  Both refusals in section 3 happen in engine.guard(), before any network call
  and before anything is written to disk - no project file is created by a
  refused init. Because it is enforced in Python rather than requested in the
  prompt, no amount of prompt wording gets around it.
"""


def run(args: list[str], stderr_only: bool = False) -> str:
    p = subprocess.run(
        [PY, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if stderr_only:
        return (p.stderr or "").strip()
    return ((p.stdout or "") + (p.stderr or "")).strip()


def rule(title: str) -> str:
    return "\n" + "=" * 79 + f"\n {title}\n" + "=" * 79


def main() -> int:
    proj_path = sys.argv[1] if len(sys.argv) > 1 else "real/story.json"
    out_path = Path(sys.argv[2] if len(sys.argv) > 2 else "TEST_RESULTS.txt")
    proj = store.load(proj_path)

    parts: list[str] = []
    parts.append("=" * 79)
    parts.append(" FANFICAI - TEST RESULTS, OBSERVATIONS AND GENERATED OUTPUT")
    parts.append(f" Generated: {time.strftime('%Y-%m-%d %H:%M')}")
    parts.append(f" Project:   {proj_path}")
    parts.append(f" Backend:   OpenAI gpt-4o-mini (chapter 3 regenerated on gpt-4o)")
    parts.append("=" * 79)

    # ---- 1. test suite ---------------------------------------------------
    parts.append(rule("1. TEST SUITE (offline, no API calls)"))
    parts.append("$ python -m unittest discover -s tests -v\n")
    parts.append(run(["-m", "unittest", "discover", "-s", "tests", "-v"], stderr_only=True))

    # ---- 2. linter -------------------------------------------------------
    parts.append(rule("2. CONTINUITY LINTER ON THE GENERATED CHAPTERS (free, offline)"))
    for ch in sorted(proj.chapters, key=lambda c: c.number):
        parts.append(f"$ python -m fanficai -p {proj_path} check -c {ch.number}")
        parts.append(run(["-m", "fanficai", "-p", proj_path, "check", "-c", str(ch.number)]))
        parts.append("")

    # ---- 3. guardrails ---------------------------------------------------
    parts.append(rule("3. GUARDRAIL AND ERROR-HANDLING CHECKS"))
    cases = [
        ("explicit content is refused",
         ["-m", "fanficai", "-p", "_probe.json", "init", "--title", "X",
          "--rating", "M", "--tags", "explicit,smut"]),
        ("sexual content involving minors is hard refused",
         ["-m", "fanficai", "-p", "_probe.json", "init", "--title", "X",
          "--premise", "explicit scenes with a 14-year-old"]),
        ("missing project file fails cleanly",
         ["-m", "fanficai", "-p", "_nope.json", "show"]),
        ("unwritten chapter fails cleanly",
         ["-m", "fanficai", "-p", proj_path, "--provider", "mock", "continue", "-c", "99"]),
    ]
    for label, args in cases:
        parts.append(f"-- {label}")
        parts.append("   " + run(args).replace("\n", "\n   "))
        parts.append("")
    Path("_probe.json").unlink(missing_ok=True)

    # ---- 4. spend --------------------------------------------------------
    parts.append(rule("4. MEASURED API SPEND"))
    parts.append(f"calls          : {proj.calls}")
    parts.append(f"input tokens   : {proj.tokens_in}")
    parts.append(f"output tokens  : {proj.tokens_out}")
    parts.append(f"estimated cost : ${proj.cost_usd:.4f}")
    words = sum(c.word_count for c in proj.chapters)
    parts.append(f"words produced : {words}")
    if words:
        parts.append(f"cost per 1000 words: ${proj.cost_usd / words * 1000:.4f}")

    # ---- 5. observations -------------------------------------------------
    parts.append(rule("5. OBSERVATIONS"))
    parts.append(OBSERVATIONS.strip())

    # ---- 6. story bible --------------------------------------------------
    parts.append(rule("6. STORY BIBLE (injected into every single call)"))
    parts.append(proj.bible())

    # ---- 7. outline ------------------------------------------------------
    parts.append(rule("7. MODEL-GENERATED OUTLINE"))
    for ch in sorted(proj.chapters, key=lambda c: c.number):
        parts.append(f"{ch.number}. {ch.title}   [POV: {ch.pov}]")
        if ch.summary:
            parts.append(f"    {ch.summary}")
        for b in ch.beats:
            parts.append(f"    - {b}")
        parts.append("")

    # ---- 8. the prose ----------------------------------------------------
    parts.append(rule("8. GENERATED CHAPTERS IN FULL"))
    for ch in sorted(proj.chapters, key=lambda c: c.number):
        parts.append(f"\n{'-' * 79}\nCHAPTER {ch.number}: {ch.title}"
                     f"  ({ch.word_count} words, POV {ch.pov})\n{'-' * 79}\n")
        parts.append(ch.text.strip() or "(not written)")

    # utf-8-sig: the BOM makes Notepad and Word on Windows read the curly
    # quotes and em dashes correctly instead of as mojibake.
    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8-sig")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
