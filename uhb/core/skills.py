"""``load_skill`` (SPEC.md §2.3).

Synchronous by contract: a model cannot wait on a skill load mid-turn. Skills are
directories holding a ``SKILL.md`` with YAML front matter; the search is bounded so
a huge workspace cannot stall a turn.
"""

from __future__ import annotations

from pathlib import Path

from .types import SkillDescriptor

MAX_SEARCH_DEPTH = 4


class UnknownSkill(KeyError):
    """Raised when no ``SKILL.md`` matches the requested identifier."""


class Skills:
    """Reads skills from one or more roots, in order, first hit wins."""

    def __init__(self, roots: list[str | Path], max_depth: int = MAX_SEARCH_DEPTH) -> None:
        self.roots = [Path(root) for root in roots]
        self.max_depth = max_depth

    # -- the pivot function ---------------------------------------------------

    def load_skill(self, skill_id: str) -> SkillDescriptor:
        skill_id = str(skill_id).strip()
        if not skill_id:
            raise UnknownSkill("empty skill id")
        path = self._find(skill_id)
        if path is None:
            searched = ", ".join(str(root) for root in self.roots) or "(no roots)"
            raise UnknownSkill(f"unknown skill {skill_id!r}; searched {searched}")
        return _descriptor(skill_id, path)

    # -- helpers --------------------------------------------------------------

    def _find(self, skill_id: str) -> Path | None:
        direct = skill_id.split(":")[0]
        for root in self.roots:
            candidate = root / direct / "SKILL.md"
            if candidate.is_file():
                return candidate
            candidate = root / skill_id / "SKILL.md"
            if candidate.is_file():
                return candidate
        for root in self.roots:
            if not root.is_dir():
                continue
            for candidate in self._walk(root, self.max_depth):
                if candidate.parent.name in {direct, skill_id}:
                    return candidate
        return None

    def _walk(self, root: Path, depth: int):
        if depth <= 0:
            return
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            candidate = entry / "SKILL.md"
            if candidate.is_file():
                yield candidate
            yield from self._walk(entry, depth - 1)


def _descriptor(skill_id: str, path: Path) -> SkillDescriptor:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = _front_matter(text)
    body = text.split("---", 2)[-1].strip() if text.lstrip().startswith("---") else text
    return SkillDescriptor(
        id=skill_id,
        name=str(meta.get("name") or path.parent.name),
        description=str(meta.get("description") or _first_line(body)),
        content=body,
    )


def _front_matter(text: str) -> dict:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml  # local import: no hard dependency for users who never load skills

        data = yaml.safe_load(parts[1]) or {}
    except Exception:  # a malformed front matter must not break the turn
        return {}
    return data if isinstance(data, dict) else {}


def _first_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:200]
    return ""
