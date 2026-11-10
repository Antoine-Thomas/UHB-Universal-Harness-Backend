"""ingest — compile one raw source into wiki pages, with a single LLM call.

This is the compile step of the Karpathy pattern: read a source, read the wiki
conventions and the current catalogue, ask the model once which pages to create
or update, then write the result atomically. There is no agent loop and no
retry: one POST, one decision, one write.

    result = ingest("raw/notes/note.md")
    result.pages_created   # ["concepts/uhb.md"]
    result.pages_updated   # ["concepts/hub.md"]
    result.links_added     # 6

Re-ingesting the same source is a no-op: the sha256 of the source is stored in
each compiled page's frontmatter, and a page already carrying that hash proves
the source has not changed since it was compiled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    LLM,
    TAG_DIRS,
    TAXONOMY,
    TAG_SECTIONS,
    SourceNotInRaw,
    WikiError,
    default_llm,
    extract_json,
    load_pages,
    page_tag,
    raw_dir,
    relative_ref,
    render_page,
    resolve_root,
    slugify,
    source_hash,
    summary_of,
    today,
    wiki_dir,
    wikilinks,
)
from ...guard import commit
from ...modes import ModeRequired, ModeSpec, get_mode
from ...router import routed_llm

#: A source longer than this is truncated for the prompt, with an explicit note.
MAX_SOURCE_CHARS = 8000

#: A single ingest may not create more than this many pages: beyond it, the
#: source is a corpus, not a note, and belongs in its own directory.
MAX_PAGES = 12

#: A compile is a bounded extraction task, whatever the size of the schema it is
#: handed. Routing it as "complex" because the prompt is long would drop the free
#: tier (the local 8B does not claim that class) and fail a compile that has a
#: perfectly good model available.
#:
#: ASSUMPTION, NOT A MEASUREMENT: no API key was configured when this was written,
#: so the flash path was never exercised on a real source. Re-evaluate in
#: production against a large source: if flash produces thin pages, raise this to
#: "complex" (the pro model answers) rather than tuning the classifier.
COMPILE_COMPLEXITY = "standard"

#: How much of the log is shown to the model, in lines.
LOG_TAIL = 40


@dataclass(slots=True)
class IngestResult:
    """What one ingestion changed. Counts are exact, paths are wiki-relative."""

    source: str
    dry_run: bool = False
    pages_created: list[str] = field(default_factory=list)
    pages_updated: list[str] = field(default_factory=list)
    links_added: int = 0
    skipped: bool = False
    reason: str = ""

    @property
    def changed(self) -> bool:
        return not self.skipped and bool(self.pages_created or self.pages_updated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "dry_run": self.dry_run,
            "pages_created": list(self.pages_created),
            "pages_updated": list(self.pages_updated),
            "links_added": self.links_added,
            "skipped": self.skipped,
            "reason": self.reason,
        }


# --- prompt ----------------------------------------------------------------


def _pages_block(pages: list[dict[str, Any]]) -> str:
    if not pages:
        return "(none yet — this is the first source)"
    return "\n".join(
        f"- {page['slug']} [{', '.join(page['meta'].get('tags') or [])}] — {page['meta'].get('title', '')}"
        for page in pages
    )


def build_prompt(
    *,
    source_ref: str,
    source_text: str,
    schema: str,
    index: str,
    log_tail: str,
    pages: list[dict[str, Any]],
) -> str:
    """The one prompt of the compile step. Deterministic, no conversation."""
    truncated = ""
    if len(source_text) > MAX_SOURCE_CHARS:
        source_text = source_text[:MAX_SOURCE_CHARS]
        truncated = (
            "\n(The source was truncated to "
            f"{MAX_SOURCE_CHARS} characters: compile what is visible.)"
        )
    return f"""You compile a wiki from immutable sources.

## Conventions (wiki/schema.md)
{schema}

## Current index (wiki/index.md)
{index}

## Recent log (wiki/log.md, tail)
{log_tail or "(empty)"}

## Existing pages
{_pages_block(pages)}

## Source to compile: {source_ref}
<<<
{source_text}
>>>{truncated}

Reply with ONE JSON object and nothing else:
{{"pages": [
  {{"slug": "kebab-case-slug",
    "title": "Human readable title",
    "tags": ["concept"],
    "links": ["other-slug", "third-slug"],
    "body": "# Human readable title\\n\\nTwo or three short paragraphs. At least two [[wikilinks]]."}}
]}}

Rules:
- tags come from this closed list: {", ".join(TAXONOMY)}
- one page per concept, entity or procedure — never one page per source
- update an existing page (same slug) instead of creating a near-duplicate
- every page needs at least two [[wikilinks]] to other pages
- body under 200 lines, written in the language of the source
- if the source adds nothing new, reply {{"pages": []}}
"""


# --- index and log ---------------------------------------------------------


def render_index(
    root: Path, day: str | None = None, pages: list[dict[str, Any]] | None = None
) -> str:
    """Rebuild ``index.md`` from the pages on disk, or from a planned page list.

    The catalogue is derived, never hand-edited: a page that exists is listed, a
    page that was deleted disappears. ``pages`` lets a caller render the index
    *before* the pages are written, which is what makes a gated compile
    all-or-nothing: the index never describes a page that was refused.
    """
    day = day or today()
    pages = load_pages(root) if pages is None else pages
    grouped: dict[str, list[dict[str, Any]]] = {tag: [] for tag in TAG_DIRS}
    for page in pages:
        grouped.setdefault(page_tag(page["path"], page["meta"]), []).append(page)

    lines = [
        "# Wiki Index",
        "",
        "> Catalogue of compiled pages: one line per page. Read this file first,",
        "> then open only the pages it points at. Generated by `ingest`; a page",
        "> missing here is a page nobody will find (see `lint`).",
        "",
        f"Last updated: {day} | Total pages: {len(pages)}",
        "",
    ]
    for tag in TAG_DIRS:
        lines.append(f"## {TAG_SECTIONS[tag]}")
        entries = sorted(grouped.get(tag, []), key=lambda page: page["slug"])
        if not entries:
            lines.append("<!-- empty -->")
        for page in entries:
            lines.append(f"- [[{page['slug']}]] — {summary_of(page['body'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_index(root: Path, mode: ModeSpec | str, day: str | None = None) -> Path:
    """Rewrite the catalogue alone — a maintenance write, gated like any other."""
    path = wiki_dir(root) / "index.md"
    commit(mode, [(path, render_index(root, day))], project=root)
    return path


def _log_tail(root: Path) -> str:
    path = wiki_dir(root) / "log.md"
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-LOG_TAIL:])


def _log_text(root: Path, block: str) -> str:
    """The log after appending ``block`` — computed, not written."""
    path = wiki_dir(root) / "log.md"
    existing = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    if not existing:
        existing = "# Wiki Log\n\n> Append-only. One entry per operation, newest at the bottom.\n"
    separator = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + separator + block


# --- page assembly ---------------------------------------------------------


def _ensure_links(body: str, links: list[str]) -> str:
    """Guarantee the two-wikilink rule when the model forgot it."""
    body = body.strip()
    present = set(wikilinks(body))
    missing = [link for link in links if link and link not in present]
    if len(present) + len(missing) < 2 or not missing:
        return body
    block = "\n\n## See also\n\n" + "\n".join(f"- [[{link}]]" for link in missing[:6])
    return body + block


def _valid_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip().lower() in TAXONOMY:
            tag = item.strip().lower()
            if tag not in tags:
                tags.append(tag)
    return tags


def _clean_items(reply: str) -> tuple[list[dict[str, Any]], int]:
    """Turn a raw reply into writable page specs. Returns ``(items, rejected)``."""
    data = extract_json(reply)
    raw_items = data.get("pages")
    if raw_items is None:
        raise WikiError("the model reply has no 'pages' key")
    if not isinstance(raw_items, list):
        raise WikiError("'pages' must be a list")

    items: list[dict[str, Any]] = []
    rejected = 0
    seen: set[str] = set()
    for raw in raw_items:
        if len(items) >= MAX_PAGES:
            rejected += 1
            continue
        if not isinstance(raw, dict):
            rejected += 1
            continue
        title = str(raw.get("title") or "").strip()
        body = str(raw.get("body") or "").strip()
        tags = _valid_tags(raw.get("tags"))
        slug = slugify(str(raw.get("slug") or title))
        if not title or not body or not tags or slug in seen:
            rejected += 1
            continue
        links = [slugify(str(link)) for link in (raw.get("links") or []) if str(link).strip()]
        seen.add(slug)
        items.append(
            {"slug": slug, "title": title, "tags": tags, "body": body, "links": links}
        )
    return items, rejected


def _new_meta(item: dict[str, Any], ref: str, digest: str, day: str) -> dict[str, Any]:
    return {
        "title": item["title"],
        "tags": item["tags"],
        "sources": [ref],
        "created": day,
        "updated": day,
        "contested": False,
        "source_hashes": {ref: digest},
    }


def _merged_meta(
    old: dict[str, Any], item: dict[str, Any], ref: str, digest: str, day: str
) -> dict[str, Any]:
    """Update keeps identity (created, contested), unions provenance."""
    tags = list(old.get("tags") or [])
    for tag in item["tags"]:
        if tag not in tags:
            tags.append(tag)
    sources = list(old.get("sources") or [])
    if ref not in sources:
        sources.append(ref)
    hashes = dict(old.get("source_hashes") or {})
    hashes[ref] = digest
    return {
        "title": item["title"],
        "tags": tags,
        "sources": sources,
        "created": old.get("created") or day,
        "updated": day,
        "contested": bool(old.get("contested")),
        "source_hashes": hashes,
    }


# --- the operation ---------------------------------------------------------


def ingest(
    source_path: str | Path,
    dry_run: bool = False,
    *,
    workspace: str | Path | None = None,
    llm: LLM | None = None,
    mode: ModeSpec | str | None = None,
    project: str | Path | None = None,
) -> IngestResult:
    """Compile ``source_path`` into pages under ``workspace/wiki/``.

    ``dry_run`` plans and returns exactly what would be written, without
    touching the disk. ``llm`` is injectable so tests never reach the network.

    ``mode`` is the permission envelope and it is **mandatory**: every planned
    path is checked with :func:`uhb.hub.guard.commit` *before* the first byte is
    written, so a read-only mode leaves the tree untouched instead of
    half-compiled. Omitting it raises :class:`ModeRequired`, never "no policy".
    ``project`` is the tree the mode's globs are written against, when the wiki
    lives inside a larger repository.
    """
    root = resolve_root(workspace)
    raw = raw_dir(root)
    # Resolve the envelope first: a missing or unknown mode must fail before a
    # model call and before any write, not after both.
    if mode is None:
        raise ModeRequired(
            "ingest() needs a mode: pass get_mode('dev') (or another profile) so the "
            "write scope is decided before anything is compiled"
        )
    spec = get_mode(mode) if isinstance(mode, str) else mode

    path = Path(source_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"source not found: {path}")
    if raw.resolve() not in path.parents:
        raise SourceNotInRaw(
            f"source must live under {raw} — copy it there first, sources are immutable"
        )
    ref = relative_ref(root, path)

    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return IngestResult(source=ref, dry_run=dry_run, skipped=True, reason="empty source")

    digest = source_hash(text)
    pages = load_pages(root)
    tracked = [page for page in pages if ref in (page["meta"].get("sources") or [])]
    if tracked:
        hashes = {(page["meta"].get("source_hashes") or {}).get(ref) for page in tracked}
        if hashes == {digest}:
            return IngestResult(
                source=ref,
                dry_run=dry_run,
                skipped=True,
                reason=f"unchanged (sha256 {digest[:12]} already compiled into "
                f"{len(tracked)} page(s))",
            )

    schema_path = wiki_dir(root) / "schema.md"
    schema = schema_path.read_text(encoding="utf-8", errors="replace") if schema_path.is_file() else ""
    index_path = wiki_dir(root) / "index.md"
    index = index_path.read_text(encoding="utf-8", errors="replace") if index_path.is_file() else ""

    prompt = build_prompt(
        source_ref=ref,
        source_text=text,
        schema=schema,
        index=index,
        log_tail=_log_tail(root),
        pages=pages,
    )
    # With a mode, the model is chosen by the router (tier + complexity) and the
    # call falls back down the chain. Without one, the caller supplied the LLM or
    # the plain default endpoint is used — no policy, no fallback.
    if llm is not None:
        reply = llm(prompt)
    elif spec is not None:
        reply = routed_llm(spec, complexity=COMPILE_COMPLEXITY)(prompt)
    else:
        reply = default_llm(prompt)
    items, rejected = _clean_items(reply)
    if not items:
        return IngestResult(
            source=ref,
            dry_run=dry_run,
            skipped=True,
            reason=f"the model proposed no usable page ({rejected} rejected)",
        )

    by_slug = {page["slug"]: page for page in pages}
    day = today()
    created: list[str] = []
    updated: list[str] = []
    links_added = 0
    planned: list[tuple[Path, str]] = []
    # The catalogue is rendered from this in-memory view, not from the disk:
    # an index describing a page that was never written is a broken catalogue.
    records: dict[str, dict[str, Any]] = {page["slug"]: dict(page) for page in pages}

    for item in items:
        body = _ensure_links(item["body"], item["links"])
        existing = by_slug.get(item["slug"])
        if existing is not None:
            meta = _merged_meta(existing["meta"], item, ref, digest, day)
            before = set(wikilinks(existing["body"]))
            links_added += len(set(wikilinks(body)) - before)
            target = existing["path"]
            records[item["slug"]] = {
                "path": target,
                "ref": existing["ref"],
                "slug": item["slug"],
                "meta": meta,
                "body": body,
            }
            updated.append(existing["ref"])
        else:
            meta = _new_meta(item, ref, digest, day)
            links_added += len(set(wikilinks(body)))
            target = wiki_dir(root) / TAG_DIRS[item["tags"][0]] / f"{item['slug']}.md"
            records[item["slug"]] = {
                "path": target,
                "ref": target.relative_to(wiki_dir(root)).as_posix(),
                "slug": item["slug"],
                "meta": meta,
                "body": body,
            }
            created.append(target.relative_to(wiki_dir(root)).as_posix())
        planned.append((target, render_page(meta, body)))

    log_block = "\n".join(
        [
            f"## [{day}] ingest | {ref}",
            "",
            f"- Created: {len(created)}" + (f" ({', '.join(created)})" if created else ""),
            f"- Updated: {len(updated)}" + (f" ({', '.join(updated)})" if updated else ""),
            f"- Links added: {links_added}",
            f"- Source sha256: {digest[:12]}",
            "",
        ]
    )
    planned.append((wiki_dir(root) / "index.md", render_index(root, day, pages=list(records.values()))))
    planned.append((wiki_dir(root) / "log.md", _log_text(root, log_block)))

    reason = f"{rejected} page(s) rejected by the schema" if rejected else ""
    if not dry_run:
        commit(spec, planned, project=project if project is not None else root)

    return IngestResult(
        source=ref,
        dry_run=dry_run,
        pages_created=created,
        pages_updated=updated,
        links_added=links_added,
        skipped=False,
        reason=reason,
    )
