"""Orchestration: turns a Project + a provider into outlines, drafts and notes."""

from __future__ import annotations

import re

from . import prompts
from .models import ChapterPlan, Project
from .providers import Provider

# --------------------------------------------------------------------------
# Content guardrails
# --------------------------------------------------------------------------
EXPLICIT_REQUEST = re.compile(
    r"\b(explicit|smut|pwp|graphic sex|sex scene|nsfw|lemon)\b", re.I
)
MINOR_MARKERS = re.compile(r"\b(child|kid|minor|underage|\d{1,2}[- ]year[- ]old)\b", re.I)


class SafetyRefusal(Exception):
    pass


def guard(project: Project, extra: str = "") -> None:
    """Block the two things this tool will not do, regardless of prompt."""
    blob = " ".join([project.premise, project.tone, extra, *project.tags]).lower()
    if MINOR_MARKERS.search(blob) and EXPLICIT_REQUEST.search(blob):
        raise SafetyRefusal(
            "Refusing: sexual content involving minors is off limits, full stop."
        )
    if EXPLICIT_REQUEST.search(blob):
        raise SafetyRefusal(
            "Refusing: this tool tops out at rating M with fade-to-black. "
            "Remove the explicit tag, or write that part yourself."
        )
    if project.rating not in ("G", "T", "M"):
        raise SafetyRefusal(f"Unsupported rating {project.rating!r}; use G, T or M.")


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def make_outline(project: Project, provider: Provider, n_chapters: int) -> list[ChapterPlan]:
    guard(project)
    raw = provider.generate(
        prompts.SYSTEM, prompts.outline_prompt(project, n_chapters), max_tokens=1500
    )
    chapters = parse_outline(raw)
    if not chapters:  # model ignored the format; keep something usable
        chapters = [ChapterPlan(number=i, title=f"Chapter {i}") for i in range(1, n_chapters + 1)]
    return chapters


def parse_outline(raw: str) -> list[ChapterPlan]:
    chapters: list[ChapterPlan] = []
    current: ChapterPlan | None = None
    for line in raw.splitlines():
        line = line.strip()
        head = re.match(r"^#{1,3}\s*(\d+)\s*[.:)-]\s*(.*)$", line)
        if head:
            current = ChapterPlan(number=int(head.group(1)), title=head.group(2).strip())
            chapters.append(current)
            continue
        if current is None:
            continue
        if line.upper().startswith("POV:"):
            current.pov = line[4:].strip()
        elif line.upper().startswith("SUMMARY:"):
            current.summary = line[8:].strip()
        elif line.startswith(("- ", "* ")):
            current.beats.append(line[2:].strip())
    return chapters


def write_chapter(
    project: Project, provider: Provider, number: int, words: int = 800
) -> ChapterPlan:
    guard(project)
    ch = project.chapter(number)
    if ch is None:
        ch = ChapterPlan(number=number, title=f"Chapter {number}")
        project.chapters.append(ch)
        project.chapters.sort(key=lambda c: c.number)
    if not ch.pov:
        ch.pov = project.default_pov_character()
    ch.text = provider.generate(
        prompts.SYSTEM,
        prompts.chapter_prompt(project, ch, words),
        max_tokens=_tokens(words),
    )
    ch.touch()
    return ch


def continue_chapter(
    project: Project, provider: Provider, number: int, words: int = 300, note: str = ""
) -> ChapterPlan:
    guard(project, note)
    ch = project.chapter(number)
    if ch is None or not ch.text:
        raise ValueError(f"Chapter {number} has no draft yet; use `write` first.")
    more = provider.generate(
        prompts.SYSTEM,
        prompts.continue_prompt(project, ch, words, note),
        max_tokens=_tokens(words),
    )
    ch.text = ch.text.rstrip() + "\n\n" + more.strip()
    ch.touch()
    return ch


def critique(project: Project, provider: Provider, number: int) -> str:
    ch = project.chapter(number)
    if ch is None or not ch.text:
        raise ValueError(f"Chapter {number} has no draft yet.")
    return provider.generate(
        prompts.SYSTEM, prompts.critique_prompt(project, ch), max_tokens=1200
    )


def _tokens(words: int) -> int:
    return max(600, int(words * 2.2))


def record_usage(project: Project, provider: Provider) -> str:
    """Fold the last call's token usage into the project's running total.
    Returns a one-line summary for the CLI to show."""
    u = getattr(provider, "last_usage", {"in": 0, "out": 0})
    if not (u["in"] or u["out"]):
        return ""
    cost = provider.last_cost()
    project.tokens_in += u["in"]
    project.tokens_out += u["out"]
    project.cost_usd = round(project.cost_usd + cost, 6)
    project.calls += 1
    money = f"~${cost:.4f}" if cost else "cost unknown for this model"
    return (
        f"[{u['in']} in / {u['out']} out tokens, {money} "
        f"| project total: {project.calls} calls, ~${project.cost_usd:.4f}]"
    )


# --------------------------------------------------------------------------
# Local (no-model) continuity linting
# --------------------------------------------------------------------------
FILTER_VERBS = re.compile(
    r"\b(felt|noticed|realized|realised|saw that|seemed to|began to|started to)\b", re.I
)
FIRST_PERSON = re.compile(r"\b(I|my|me)\b")
PRESENT_TENSE_HINT = re.compile(r"\b(walks|says|looks|turns|thinks|is standing)\b", re.I)

# Capitalised words that are never character names, in lowercase form.
STOPWORDS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "god", "christ", "jesus",
}


def lint_chapter(project: Project, number: int) -> list[str]:
    """Cheap deterministic checks. Catches the mistakes models actually make,
    without spending a token."""
    ch = project.chapter(number)
    if ch is None or not ch.text:
        return [f"Chapter {number}: no draft to check."]

    notes: list[str] = []
    text = ch.text
    # POV, tense and cast rules apply to narration, not to what characters say
    # out loud, so quoted dialogue is stripped first. Only double quotes count
    # as delimiters: a single quote is far more often an apostrophe ("it's"),
    # and treating it as a quote mark truncates the strip and leaks dialogue
    # back into the "narration" we lint.
    narration = re.sub(r"[\"\u201c][^\"\u201c\u201d]*[\"\u201d]", " ", text)

    if ch.word_count and abs(ch.word_count - len(text.split())) > 5:
        notes.append("Stored word_count is stale; re-save the project.")

    # POV drift
    if project.pov.startswith("third") and FIRST_PERSON.search(narration):
        notes.append("POV: first-person pronouns in third-person narration.")
    if project.pov.startswith("first") and not FIRST_PERSON.search(narration):
        notes.append("POV: first-person story with no first-person narration.")

    # Tense drift
    if project.tense == "past" and len(PRESENT_TENSE_HINT.findall(narration)) > 2:
        notes.append("Tense: several present-tense verbs in a past-tense story.")

    # Cast validation. A capitalised word only counts as a candidate name when
    # it appears mid-sentence, i.e. preceded by a lowercase word or a comma.
    # Sentence-initial capitals are just grammar and would flood the report.
    known = {c.name.lower() for c in project.characters}
    candidates = {
        w for w in re.findall(r"(?<=[a-z,] )([A-Z][a-z]{2,})", narration)
        if w.lower() not in known
    }
    # A word that also appears lowercase elsewhere is a common noun, not a name.
    lowered = set(re.findall(r"\b([a-z]{3,})\b", text))
    invented = sorted(c for c in candidates if c.lower() not in lowered | STOPWORDS)
    if invented:
        notes.append(
            "Possible off-bible names: " + ", ".join(invented[:8])
            + " (add to the cast with `char`, or rename)"
        )

    # Craft
    filters = FILTER_VERBS.findall(text)
    if len(filters) > max(3, ch.word_count // 250):
        notes.append(f"Craft: {len(filters)} filter verbs ('felt', 'noticed'...). Trim them.")

    for banned in project.avoid:
        if banned and banned.lower() in text.lower():
            notes.append(f"Bible violation: contains '{banned}' from the avoid list.")

    if not notes:
        notes.append("No issues found by the local checks.")
    return notes


# --------------------------------------------------------------------------
def export_markdown(project: Project) -> str:
    out = [f"# {project.title}", ""]
    meta = [f"**Fandom:** {project.fandom}", f"**Rating:** {project.rating}"]
    if project.pairing:
        meta.append(f"**Pairing:** {project.pairing}")
    if project.tags:
        meta.append(f"**Tags:** {', '.join(project.tags)}")
    out += [" | ".join(meta), ""]
    if project.premise:
        out += [f"*{project.premise}*", ""]
    for ch in sorted(project.chapters, key=lambda c: c.number):
        out += [f"## {ch.number}. {ch.title}".rstrip(), ""]
        out += [ch.text.strip() or "*(not written yet)*", ""]
    return "\n".join(out)
