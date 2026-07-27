"""Load/save a Project as JSON on disk."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Project

DEFAULT_FILE = "story.json"


class ProjectNotFound(Exception):
    pass


def path_for(project_path: str | None) -> Path:
    return Path(project_path or DEFAULT_FILE)


def save(project: Project, project_path: str | None = None) -> Path:
    p = path_for(project_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(project.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(p)  # atomic-ish write so a crash can't shred the story
    return p


def load(project_path: str | None = None) -> Project:
    p = path_for(project_path)
    if not p.exists():
        raise ProjectNotFound(
            f"No project at {p}. Run `fanficai init --title \"...\"` first."
        )
    return Project.from_dict(json.loads(p.read_text(encoding="utf-8")))
