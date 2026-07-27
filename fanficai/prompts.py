"""Prompt construction. All model-facing text lives here so it can be tuned
without touching the engine."""

from __future__ import annotations

from .models import ChapterPlan, Project

SYSTEM = """You are a fanfiction writing partner. You help a human author draft
their story: you propose outlines, draft scenes in their chosen voice, and give
craft feedback.

Hard rules:
- Stay inside the story bible you are given (POV, tense, rating, cast, tags).
- Never write sexually explicit content, and never sexualise a character who is
  a minor in canon. If asked, decline and offer a fade-to-black alternative.
- Transformative fan work only: no reproducing long verbatim passages from the
  source material.
- Prose only in drafting mode. No commentary, no headings, no "here is".
"""


def outline_prompt(project: Project, n_chapters: int) -> str:
    return f"""{project.bible()}

Draft a {n_chapters}-chapter outline for this story.

Return exactly {n_chapters} blocks in this format, nothing else:

## <chapter number>. <chapter title>
POV: <character name>
SUMMARY: <one or two sentences>
- <story beat>
- <story beat>
- <story beat>
"""


def chapter_prompt(project: Project, ch: ChapterPlan, words: int) -> str:
    beats = "\n".join(f"- {b}" for b in ch.beats) or "- (no beats planned; improvise)"
    prev = _previous_tail(project, ch.number)
    return f"""{project.bible()}

{prev}WRITE CHAPTER {ch.number}: {ch.title or "(untitled)"}
POV CHARACTER: {ch.pov or project.default_pov_character()}
TARGET LENGTH: about {words} words
SUMMARY: {ch.summary or "(none)"}
BEATS TO HIT, IN ORDER:
{beats}

Open in the middle of action or image, not with the weather. Use concrete
sensory detail. Let dialogue carry character. End on a line that pulls the
reader forward. Prose only.
"""


def continue_prompt(project: Project, ch: ChapterPlan, words: int, note: str) -> str:
    tail = " ".join(ch.text.split()[-350:])
    return f"""{project.bible()}

You are continuing a chapter already in progress. Here are its last words:

<<<
{tail}
>>>

Continue seamlessly for about {words} more words. Do not repeat or summarise
what came before; pick up mid-motion.
{f"AUTHOR NOTE: {note}" if note else ""}
Prose only.
"""


def critique_prompt(project: Project, ch: ChapterPlan) -> str:
    return f"""{project.bible()}

Give the author craft feedback on the draft below. Be specific and kind but
honest. Cover, in this order:
1. What is working (2 bullets)
2. Characterisation drift from the bible (or "none spotted")
3. Pacing and structure
4. Prose-level habits to fix, with one rewritten example line
5. The single highest-value change to make next

DRAFT:
<<<
{ch.text}
>>>
"""


def _previous_tail(project: Project, number: int) -> str:
    prev = project.chapter(number - 1)
    if not prev or not prev.text:
        return ""
    tail = " ".join(prev.text.split()[-200:])
    return f"HOW THE PREVIOUS CHAPTER ENDED:\n<<<\n{tail}\n>>>\n\n"
