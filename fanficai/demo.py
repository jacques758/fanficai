from __future__ import annotations

from dataclasses import dataclass

from . import engine
from .models import Character, Project
from .providers import MockProvider


@dataclass(frozen=True)
class DemoResult:
    project: Project
    checks: list[str]
    markdown: str


def create_demo_story(
    *,
    title: str,
    protagonist: str,
    traits: list[str],
    premise: str,
    tone: str,
    chapters: int,
    words: int,
) -> DemoResult:
    """Build a deterministic, no-key story demo without writing visitor data."""
    if not title.strip() or not protagonist.strip():
        raise ValueError("Title and protagonist are required.")
    if not 2 <= chapters <= 6:
        raise ValueError("Choose between 2 and 6 chapters.")
    if not 200 <= words <= 900:
        raise ValueError("Choose between 200 and 900 words.")

    project = Project(
        title=title.strip(),
        fandom="Original",
        rating="T",
        tone=tone.strip() or "character-driven",
        premise=premise.strip(),
    )
    project.characters.append(
        Character(
            name=protagonist.strip(),
            role="protagonist",
            traits=[trait.strip() for trait in traits if trait.strip()],
        )
    )
    engine.guard(project)
    provider = MockProvider(seed=7)
    project.chapters = engine.make_outline(project, provider, chapters)
    engine.write_chapter(project, provider, 1, words)
    return DemoResult(
        project=project,
        checks=engine.lint_chapter(project, 1),
        markdown=engine.export_markdown(project),
    )
