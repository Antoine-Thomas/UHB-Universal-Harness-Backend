"""lint — structural health of the wiki: orphans, dead links, index gaps.

Five checks, each cheap enough to run on every commit:

1. orphans — pages nothing links to, so nobody will ever reach them
2. broken_links — ``[[target]]`` with no page behind it
3. index_gaps — pages absent from ``index.md`` (invisible to ``query``)
4. invalid_frontmatter — missing field, tag outside the taxonomy, no source,
   oversized page
5. contradictions — optional, needs a model: two pages sharing a tag that say
   incompatible things. Findings are appended to ``contradictions.md`` and the
   pages are flagged ``contested: true``.

    result = lint()
    result.ok                      # True on a healthy wiki
    result.to_dict()["orphans"]    # ["concepts/forgotten.md"]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    LLM,
    MAX_PAGE_LINES,
    MIN_LINKS,
    REQUIRED_FIELDS,
    RESERVED,
    TAXONOMY,
    default_llm,
    extract_json,
    load_pages,
    read_index,
    render_page,
    resolve_root,
    today,
    wiki_dir,
    wikilinks,
)
from ...guard import commit
from ...modes import ModeRequired, ModeSpec, get_mode

#: Upper bound on model-compared page pairs, so lint stays cheap.
MAX_PAIRS = 6

#: Body characters shown to the model per page when comparing two pages.
PROMPT_BODY_CHARS = 2000

CONTRADICTIONS_HEADER = """# Contradictions

> Registry of contradictions detected by `lint` (and, in time, by the
> consolidation mode). The machine-readable truth stays the `contested: true`
> flag in each page's frontmatter; this file is the readable digest.
> Entry format: `### [YYYY-MM-DD] slug-a vs slug-b`, then subject and positions.

## Open contradictions
"""


@dataclass(slots=True)
class LintResult:
    """Exactly the five checks. Empty lists mean a healthy wiki."""

    orphans: list[str] = field(default_factory=list)
    broken_links: list[dict[str, str]] = field(default_factory=list)
    index_gaps: list[str] = field(default_factory=list)
    invalid_frontmatter: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.orphans
            or self.broken_links
            or self.index_gaps
            or self.invalid_frontmatter
            or self.contradictions
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "orphans": list(self.orphans),
            "broken_links": [dict(item) for item in self.broken_links],
            "index_gaps": list(self.index_gaps),
            "invalid_frontmatter": [dict(item) for item in self.invalid_frontmatter],
            "contradictions": [dict(item) for item in self.contradictions],
            "ok": self.ok,
        }


# --- checks 1-3: links and catalogue --------------------------------------


def _link_map(pages: list[dict[str, Any]]) -> tuple[dict[str, set[str]], list[dict[str, str]]]:
    """Inbound links per slug, plus every link that points nowhere."""
    slugs = {page["slug"] for page in pages}
    structural = {name.rsplit(".", 1)[0] for name in RESERVED}
    inbound: dict[str, set[str]] = {slug: set() for slug in slugs}
    broken: list[dict[str, str]] = []
    reported: set[tuple[str, str]] = set()
    for page in pages:
        for target in wikilinks(page["body"]):
            if target in slugs:
                if target != page["slug"]:
                    inbound[target].add(page["slug"])
            elif target not in structural and (page["ref"], target) not in reported:
                reported.add((page["ref"], target))
                broken.append({"page": page["ref"], "target": target})
    return inbound, broken


# --- check 4: frontmatter --------------------------------------------------


def _validate(page: dict[str, Any], root: Path) -> list[str]:
    meta = page["meta"]
    errors: list[str] = []

    missing = [name for name in REQUIRED_FIELDS if name not in meta]
    if missing:
        errors.append("missing field(s): " + ", ".join(missing))
    if not str(meta.get("title") or "").strip():
        errors.append("empty title")

    tags = meta.get("tags")
    if not isinstance(tags, list) or not tags:
        errors.append("tags must be a non-empty list")
    else:
        unknown = [tag for tag in tags if str(tag) not in TAXONOMY]
        if unknown:
            errors.append("tag(s) outside taxonomy: " + ", ".join(map(str, unknown)))

    sources = meta.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("no source")
    else:
        for source in sources:
            if not (root / str(source)).is_file():
                errors.append(f"missing source file: {source}")

    if len(wikilinks(page["body"])) < MIN_LINKS:
        errors.append(f"needs at least {MIN_LINKS} [[wikilinks]]")
    body_lines = len(page["body"].strip().splitlines())
    if body_lines > MAX_PAGE_LINES:
        errors.append(f"{body_lines} lines (limit {MAX_PAGE_LINES}): split the page")
    return errors


# --- check 5: contradictions ----------------------------------------------


def _candidate_pairs(pages: list[dict[str, Any]], limit: int = MAX_PAIRS) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, left in enumerate(pages):
        left_tags = set(left["meta"].get("tags") or [])
        for right in pages[index + 1 :]:
            if not left_tags & set(right["meta"].get("tags") or []):
                continue
            pairs.append((left, right))
            if len(pairs) >= limit:
                return pairs
    return pairs


def _contradiction_prompt(left: dict[str, Any], right: dict[str, Any]) -> str:
    return f"""Two pages of the same wiki share a topic. Decide whether they state
facts that cannot both be true. A difference in detail is not a contradiction;
two incompatible values, dates or outcomes are.

## Page A: {left['meta'].get('title', left['slug'])}
{left['body'][:PROMPT_BODY_CHARS]}

## Page B: {right['meta'].get('title', right['slug'])}
{right['body'][:PROMPT_BODY_CHARS]}

Reply with ONE JSON object and nothing else:
{{"contradiction": false, "subject": ""}}
Set "contradiction" to true only when both cannot hold, and name the subject in
ten words or fewer.
"""


def _detect_contradictions(
    pages: list[dict[str, Any]], llm: LLM
) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for left, right in _candidate_pairs(pages):
        data = extract_json(llm(_contradiction_prompt(left, right)))
        if not data.get("contradiction"):
            continue
        first, second = sorted([left["slug"], right["slug"]])
        found.append(
            {
                "a": first,
                "b": second,
                "subject": str(data.get("subject") or "unspecified").strip(),
            }
        )
    return found


def _contradictions_plan(
    root: Path, pages: list[dict[str, Any]], found: list[dict[str, str]], day: str
) -> list[tuple[Path, str]]:
    """The registry and the contested flags as *planned writes*, not writes.

    Returning plans is what lets ``lint`` refuse a read-only mode without having
    already half-updated the registry.
    """
    path = wiki_dir(root) / "contradictions.md"
    existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    has_header = "# Contradictions" in existing
    if not has_header:
        existing = CONTRADICTIONS_HEADER

    plans: list[tuple[Path, str]] = []
    if found:
        blocks = [
            "\n".join(
                [
                    f"### [{day}] {item['a']} vs {item['b']}",
                    "",
                    f"- Subject: {item['subject']}",
                    f"- Position A ({item['a']}): see wiki page",
                    f"- Position B ({item['b']}): see wiki page",
                    "- Verdict: pending",
                    "",
                ]
            )
            for item in found
        ]
        plans.append((path, existing.rstrip() + "\n\n" + "\n".join(blocks)))
    elif not path.is_file() or not has_header:
        plans.append((path, existing))

    flagged = {item["a"] for item in found} | {item["b"] for item in found}
    for page in pages:
        if page["slug"] not in flagged or page["meta"].get("contested") is True:
            continue
        meta = dict(page["meta"])
        meta["contested"] = True
        plans.append((page["path"], render_page(meta, page["body"])))
    return plans


# --- the operation ---------------------------------------------------------


def lint(
    *,
    workspace: str | Path | None = None,
    check_contradictions: bool = False,
    llm: LLM | None = None,
    mode: ModeSpec | str | None = None,
    project: str | Path | None = None,
) -> LintResult:
    """Run the five checks.

    Contradiction detection is opt-in because it costs model calls, one per
    candidate pair. It is deliberately not silent: a model reply that is not
    JSON raises instead of reporting a healthy wiki.

    ``mode`` gates the two writes this operation may make (the contradictions
    registry, and the ``contested`` flag) and it is **mandatory**: both are
    planned first and committed together, so a read-only mode raises without
    touching either, and omitting it raises :class:`ModeRequired`.
    """
    root = resolve_root(workspace)
    if mode is None:
        raise ModeRequired(
            "lint() writes the contradictions registry and the contested flags: "
            "pass a mode (get_mode('dev')) or run it from a read-only one on purpose"
        )
    spec = get_mode(mode) if isinstance(mode, str) else mode
    pages = load_pages(root)
    result = LintResult()

    inbound, broken = _link_map(pages)
    result.broken_links = broken
    result.orphans = sorted(page["ref"] for page in pages if not inbound[page["slug"]])

    catalogued = {slug for slug, _summary in read_index(root)}
    result.index_gaps = sorted(page["ref"] for page in pages if page["slug"] not in catalogued)

    for page in pages:
        errors = _validate(page, root)
        if errors:
            result.invalid_frontmatter.append({"page": page["ref"], "errors": errors})

    if check_contradictions:
        result.contradictions = _detect_contradictions(pages, llm or default_llm)
    plans = _contradictions_plan(root, pages, result.contradictions, today())
    commit(spec, plans, project=project if project is not None else root)

    return result
