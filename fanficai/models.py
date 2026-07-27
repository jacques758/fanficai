"""Data model for a fanfic project (the "story bible")."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any


SCHEMA_VERSION = 1

VALID_RATINGS = ("G", "T", "M")


@dataclass
class Character:
    name: str
    role: str = "supporting"          # protagonist / antagonist / supporting
    traits: list[str] = field(default_factory=list)
    voice: str = ""                    # how they speak
    wants: str = ""                    # driving desire
    canon_notes: str = ""              # canon facts we must not contradict

    def brief(self) -> str:
        bits = [f"{self.name} ({self.role})"]
        if self.traits:
            bits.append("traits: " + ", ".join(self.traits))
        if self.wants:
            bits.append(f"wants: {self.wants}")
        if self.voice:
            bits.append(f"voice: {self.voice}")
        if self.canon_notes:
            bits.append(f"canon: {self.canon_notes}")
        return " | ".join(bits)


@dataclass
class ChapterPlan:
    number: int
    title: str = ""
    summary: str = ""
    beats: list[str] = field(default_factory=list)
    pov: str = ""
    text: str = ""                     # generated prose
    word_count: int = 0

    def touch(self) -> None:
        self.word_count = len(self.text.split())


@dataclass
class Project:
    title: str = "Untitled"
    fandom: str = "Original"
    pairing: str = ""
    rating: str = "T"
    pov: str = "third-limited"
    tense: str = "past"
    tone: str = "warm, character-driven"
    premise: str = ""
    tags: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    characters: list[Character] = field(default_factory=list)
    chapters: list[ChapterPlan] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    schema_version: int = SCHEMA_VERSION
    created: float = field(default_factory=time.time)

    # -- lookups -----------------------------------------------------------
    def character(self, name: str) -> Character | None:
        low = name.strip().lower()
        for c in self.characters:
            if c.name.lower() == low:
                return c
        return None

    def chapter(self, number: int) -> ChapterPlan | None:
        for ch in self.chapters:
            if ch.number == number:
                return ch
        return None

    def default_pov_character(self) -> str:
        for c in self.characters:
            if c.role == "protagonist":
                return c.name
        return self.characters[0].name if self.characters else "the narrator"

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        data = dict(data)
        chars = [Character(**c) for c in data.pop("characters", [])]
        chaps = [ChapterPlan(**c) for c in data.pop("chapters", [])]
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        proj = cls(**clean)
        proj.characters = chars
        proj.chapters = chaps
        return proj

    def bible(self) -> str:
        """Compact story-bible text injected into every prompt."""
        lines = [
            f"TITLE: {self.title}",
            f"FANDOM: {self.fandom}",
            f"RATING: {self.rating} (never exceed this)",
            f"POV: {self.pov}   TENSE: {self.tense}",
            f"TONE: {self.tone}",
        ]
        if self.pairing:
            lines.append(f"PAIRING: {self.pairing}")
        if self.premise:
            lines.append(f"PREMISE: {self.premise}")
        if self.tags:
            lines.append("TAGS: " + ", ".join(self.tags))
        if self.avoid:
            lines.append("DO NOT INCLUDE: " + ", ".join(self.avoid))
        if self.characters:
            lines.append("CAST:")
            lines += [f"  - {c.brief()}" for c in self.characters]
        return "\n".join(lines)
