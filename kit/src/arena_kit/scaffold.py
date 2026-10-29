"""`arena-kit new`: copy the template arena, which runs offline (mock) before any edits."""

from __future__ import annotations

import re
from importlib.resources import as_file, files
from pathlib import Path

RENAMES = {"gitignore": ".gitignore", "env.example": ".env.example"}


def create(name: str, question: str, parent: Path, *, owner: str = "SeijiMatsumoto") -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise SystemExit("name must be lowercase letters, digits and dashes, e.g. dedup-arena")
    target = parent / name
    if target.exists():
        raise SystemExit(f"{target} already exists")
    package = name.replace("-", "_")
    title = " ".join(w.capitalize() for w in name.split("-"))
    tokens = {"__ARENA_NAME__": name, "__PACKAGE__": package, "__TITLE__": title,
              "__QUESTION__": question, "__OWNER__": owner}

    with as_file(files("arena_kit").joinpath("template")) as template:
        for src in sorted(Path(template).rglob("*")):
            if src.is_dir() or "__pycache__" in src.parts:
                continue
            rel = src.relative_to(template).as_posix().replace("__PACKAGE__", package)
            rel = rel.removesuffix(".tmpl")
            parts = rel.split("/")
            parts[-1] = RENAMES.get(parts[-1], parts[-1])
            dest = target.joinpath(*parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if parts[-1] == ".keep":
                continue
            text = src.read_text()
            for token, value in tokens.items():
                text = text.replace(token, value)
            dest.write_text(text)
    return target
