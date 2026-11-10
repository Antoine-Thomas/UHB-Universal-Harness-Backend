"""query — return candidate pages. It never answers.

An LLM wiki is useful because the retrieval step stays cheap and inspectable.
``query`` reads the index, ranks pages by keyword overlap, opens the best ones
and hands back their text with a confidence label. Synthesising an answer is the
caller's job (a skill, a mode, a harness), so the cost of a bad question is a
few file reads, not a model call.

    result = query("how does the fallback chain work?", max_pages=5)
    result.confidence          # "high" | "medium" | "low"
    [(p.path, p.title) for p in result.pages]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (
    RESERVED,
    normalize,
    read_index,
    resolve_root,
    split_frontmatter,
    wiki_dir,
)

#: Words that carry no retrieval signal, in the two languages the wiki is written in.
STOPWORDS: frozenset[str] = frozenset(
    """
    the a an and or of to in on for with without from by as at is are was were be been
    being do does did done have has had this that these those it its they them their
    what which who whom whose how why when where can could should would will shall may
    might must not no nor but if then than so such about into over under between during
    le la les un une des du de d et ou en dans sur pour par avec sans au aux ce cette ces
    cet il elle ils elles on nous vous je tu son sa ses leur leurs qui que quoi dont ou
    est sont etait ete avoir a ai as ont fait fais comment pourquoi quand quel quelle
    quels quelles plus moins tres tout tous toute toutes mais donc car ne pas se si
    """.split()
)

#: Minimum length of a keyword.
MIN_KEYWORD = 3

#: Rank levels. Higher is a stronger match; the label is what the caller reads.
EXACT = 3
PARTIAL = 2
WEAK = 1
NONE = 0

LABELS = {EXACT: "high", PARTIAL: "medium", WEAK: "low", NONE: "low"}


@dataclass(slots=True)
class Page:
    """A candidate page: enough to cite it, not enough to replace it."""

    path: str
    title: str
    excerpt: str
    tags: list[str] = field(default_factory=list)
    score: float = 0.0
    strength: int = NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "excerpt": self.excerpt,
            "tags": list(self.tags),
            "score": self.score,
        }


@dataclass(slots=True)
class QueryResult:
    """Candidates in rank order, plus the terms that produced them."""

    question: str
    pages: list[Page] = field(default_factory=list)
    confidence: str = "low"
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "confidence": self.confidence,
            "keywords": list(self.keywords),
            "pages": [page.to_dict() for page in self.pages],
        }


def keywords_of(question: str) -> list[str]:
    """Distinct content words of the question, in order, accents folded."""
    words: list[str] = []
    for word in normalize(question).split():
        if len(word) < MIN_KEYWORD or word in STOPWORDS or word in words:
            continue
        words.append(word)
    return words


def _excerpt(body: str, limit: int = 420) -> str:
    """First prose paragraph of a page, whitespace collapsed, truncated."""
    paragraphs: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ">", "-", "*", "|", "```", "<!--")):
            if paragraphs:
                break
            continue
        paragraphs.append(line)
    text = " ".join(paragraphs)
    return text[: limit - 1] + "…" if len(text) > limit else text


def _strength(question_norm: str, keywords: list[str], page: dict[str, Any], summary: str) -> tuple[int, float]:
    """Classify the match: exact title, tag, or loose keyword hit."""
    title_norm = normalize(str(page["meta"].get("title") or ""))
    slug_norm = normalize(page["slug"])
    if question_norm in (title_norm, slug_norm):
        return EXACT, 100.0
    if not keywords:
        return NONE, 0.0

    title_hits = sum(1 for word in keywords if word in title_norm or word in slug_norm)
    if title_hits == len(keywords):
        return EXACT, 90.0
    if title_hits:
        return PARTIAL, 50.0 + 10.0 * title_hits

    tag_norm = " ".join(normalize(str(tag)) for tag in (page["meta"].get("tags") or []))
    tag_hits = sum(1 for word in keywords if word in tag_norm)
    if tag_hits:
        return PARTIAL, 40.0 + 5.0 * tag_hits

    body_norm = normalize(page["body"])
    loose = sum(
        1
        for word in keywords
        if word in body_norm or word in normalize(summary) or word in slug_norm
    )
    if loose:
        return WEAK, 10.0 + float(loose)
    return NONE, 0.0


def query(
    question: str,
    max_pages: int = 5,
    *,
    workspace: str | Path | None = None,
) -> QueryResult:
    """Return the ``max_pages`` best candidates for ``question``.

    Candidates come from ``index.md`` — the index is the catalogue, so a page
    missing from it is invisible here by design (``lint`` reports such gaps).
    """
    root = resolve_root(workspace)
    result = QueryResult(question=question, keywords=keywords_of(question))
    if max_pages <= 0 or not result.keywords:
        return result

    base = wiki_dir(root)
    if not base.is_dir():
        return result

    files = {
        path.stem.strip().lower(): path
        for path in sorted(base.rglob("*.md"))
        if path.is_file()
    }
    entries = read_index(root)
    if not entries:  # a wiki without an index is still searchable
        entries = [
            (slug, "")
            for slug, path in files.items()
            if path.name.lower() not in RESERVED
        ]

    question_norm = normalize(question)
    candidates: list[Page] = []
    seen: set[str] = set()
    for slug, summary in entries:
        path = files.get(slug)
        if path is None or slug in seen:
            continue
        seen.add(slug)
        meta, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        page = {"slug": slug, "meta": meta, "body": body}
        strength, score = _strength(question_norm, result.keywords, page, summary)
        if strength == NONE:
            continue
        candidates.append(
            Page(
                path=path.relative_to(base).as_posix(),
                title=str(meta.get("title") or slug),
                excerpt=_excerpt(body),
                tags=[str(tag) for tag in (meta.get("tags") or [])],
                score=score,
                strength=strength,
            )
        )

    candidates.sort(key=lambda page: (-page.score, page.path))
    result.pages = candidates[:max_pages]
    result.confidence = LABELS[result.pages[0].strength] if result.pages else "low"
    return result
