"""Storage primitives shared by the three wiki operations.

The wiki has three layers — ``raw/`` for immutable sources, ``wiki/`` for the
compiled pages that are derived from them, ``wiki/schema.md`` for the
conventions both obey. This module owns the plumbing every operation needs
(frontmatter, atomic writes, page discovery, wikilinks, one LLM call) so that
``ingest``, ``query`` and ``lint`` each stay one readable operation instead of
three copies of the same code.

Nothing here touches the network unless ``default_llm()`` is called.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Callable

# --- the conventions, in one place ----------------------------------------

#: Closed tag taxonomy. A page carries at least one of these, nothing else.
TAXONOMY: tuple[str, ...] = ("concept", "entity", "procedure", "reference")

#: Where a page of a given tag lives, relative to ``wiki/``.
TAG_DIRS: dict[str, str] = {
    "concept": "concepts",
    "entity": "entities",
    "procedure": "procedures",
    "reference": "references",
}

#: Section heading used by ``index.md`` for a given tag.
TAG_SECTIONS: dict[str, str] = {
    "concept": "Concepts",
    "entity": "Entities",
    "procedure": "Procedures",
    "reference": "References",
}

#: Files under ``wiki/`` that structure the wiki instead of being pages.
RESERVED: tuple[str, ...] = ("index.md", "log.md", "schema.md", "contradictions.md")

#: Keys every page frontmatter must carry.
REQUIRED_FIELDS: tuple[str, ...] = (
    "title",
    "tags",
    "sources",
    "created",
    "updated",
    "contested",
)

#: Order used when writing frontmatter, so diffs stay readable.
FRONTMATTER_ORDER: tuple[str, ...] = REQUIRED_FIELDS + ("source_hashes",)

#: Minimum wikilinks per page (schema rule).
MIN_LINKS = 2

#: Maximum body length of a page, in lines (schema rule: split beyond).
MAX_PAGE_LINES = 200

# --- LLM coordinates -------------------------------------------------------

#: Base URL of an OpenAI-compatible endpoint. Defaults to local Ollama.
BASE_URL_ENV = "UHB_LLM_BASE_URL"
MODEL_ENV = "UHB_LLM_MODEL"
TOKEN_ENV = "UHB_LLM_TOKEN"
#: Compiling a source is extraction, not deliberation: thinking models are asked
#: to skip the trace. Measured on qwen3:8b: 2.2 s with "none", 34.9 s without.
REASONING_ENV = "UHB_LLM_REASONING"
DEFAULT_REASONING = "none"
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 120.0

#: An LLM is a plain callable: prompt in, raw text out.
LLM = Callable[[str], str]

#: Workspace root of this wiki: the directory holding ``raw/`` and ``wiki/``.
DEFAULT_ROOT = Path(__file__).resolve().parents[1]


class WikiError(RuntimeError):
    """Base class for wiki failures."""


class LLMReplyError(WikiError):
    """The model answered something that is not the expected JSON object."""


class SourceNotInRaw(WikiError, ValueError):
    """Sources are immutable inputs, so they live under ``raw/``."""


# --- workspace layout ------------------------------------------------------


def resolve_root(workspace: str | Path | None = None) -> Path:
    """Return the wiki root, defaulting to the one this package ships with."""
    if workspace is None:
        return DEFAULT_ROOT
    return Path(workspace).expanduser().resolve()


def wiki_dir(root: Path) -> Path:
    return root / "wiki"


def raw_dir(root: Path) -> Path:
    return root / "raw"


def ensure_tree(root: Path) -> Path:
    """Create the three layers if missing. Idempotent."""
    (root / "raw" / "notes").mkdir(parents=True, exist_ok=True)
    for sub in TAG_DIRS.values():
        (wiki_dir(root) / sub).mkdir(parents=True, exist_ok=True)
    return root


def relative_ref(root: Path, path: Path) -> str:
    """Posix path relative to the root — never an absolute path on disk."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:  # pragma: no cover - defensive
        raise SourceNotInRaw(f"source must live under {root}") from exc


# --- frontmatter -----------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


def _unquote(token: str) -> str:
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _split_flow(inner: str) -> list[str]:
    """Split a ``[a, b]`` list body on commas that are not inside quotes."""
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    for char in inner:
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return [p for p in (part.strip() for part in parts) if p]


def _parse_scalar(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [_unquote(part) for part in _split_flow(raw[1:-1])]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in ("null", "~"):
        return None
    return _unquote(raw)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a page into ``(frontmatter, body)``.

    The parser covers the subset the schema allowed itself: scalars, booleans,
    flow lists, and one level of nested map (``source_hashes``). That subset has
    no ambiguity, so no YAML engine is needed and no dependency is added.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    nested_key: str | None = None
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        if line[:1] in (" ", "\t") and nested_key is not None:
            key, sep, value = line.strip().partition(":")
            if sep:
                meta[nested_key][key.strip()] = _parse_scalar(value)
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        name = key.strip()
        raw = value.strip()
        if raw == "":
            meta[name] = {}
            nested_key = name
        else:
            meta[name] = _parse_scalar(raw)
            nested_key = None
    return meta, text[match.end():]


_MUST_QUOTE = re.compile(r"[:#\[\]{}&*!|>'\"%@`,\s]")

#: Keys written quoted even when they would parse unquoted: dates would be read
#: as date objects by a YAML engine, titles benefit from explicit boundaries.
ALWAYS_QUOTED = frozenset({"title", "created", "updated"})


def _format_scalar(value: Any, force_quote: bool = False) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return ""
    text = str(value)
    if force_quote or text == "" or _MUST_QUOTE.search(text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _format_value(key: str, value: Any) -> str:
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            # sources are paths: quoting them keeps the file readable and
            # unambiguous even when a path grows a colon or a space.
            items.append(_format_scalar(item, force_quote=(key == "sources")))
        return "[" + ", ".join(items) + "]"
    return _format_scalar(value, force_quote=(key in ALWAYS_QUOTED))


def _format_map(value: dict[str, Any]) -> list[str]:
    """One level of nesting, as indented ``sub: value`` lines."""
    return [
        f"  {sub_key}: {_format_scalar(sub_value, force_quote=True)}"
        for sub_key, sub_value in value.items()
    ]


def dump_frontmatter(meta: dict[str, Any]) -> str:
    """Render frontmatter with a stable key order."""
    lines = ["---"]
    ordered = [key for key in FRONTMATTER_ORDER if key in meta]
    ordered += [key for key in meta if key not in FRONTMATTER_ORDER]
    for key in ordered:
        value = meta[key]
        if isinstance(value, dict):
            if value:
                lines.append(f"{key}:")
                lines.extend(_format_map(value))
            else:
                lines.append(f"{key}: {{}}")
        else:
            lines.append(f"{key}: {_format_value(key, value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def render_page(meta: dict[str, Any], body: str) -> str:
    """Full page text: frontmatter, blank line, body."""
    return dump_frontmatter(meta) + "\n" + body.strip() + "\n"


# --- pages -----------------------------------------------------------------


def page_slug(path: Path) -> str:
    return path.stem.strip().lower()


def page_tag(path: Path, meta: dict[str, Any]) -> str:
    """Primary tag of a page: its directory if it is a known one, else its first tag."""
    folder = path.parent.name
    for tag, folder_name in TAG_DIRS.items():
        if folder == folder_name:
            return tag
    tags = meta.get("tags") or []
    return tags[0] if tags and tags[0] in TAXONOMY else "concept"


def iter_page_files(root: Path) -> list[Path]:
    """Every page under ``wiki/``, ordered, structural files excluded.

    Only the root copies of ``index.md``, ``log.md``, ``schema.md`` and
    ``contradictions.md`` are structure: a page that happens to share one of
    those names in a tag directory is still a page, and is compiled as such.
    """
    base = wiki_dir(root)
    if not base.is_dir():
        return []
    return sorted(
        path
        for path in base.rglob("*.md")
        if path.is_file() and not (path.parent == base and path.name.lower() in RESERVED)
    )


def load_pages(root: Path) -> list[dict[str, Any]]:
    """Read every page once: ``{path, ref, slug, meta, body}``."""
    pages = []
    for path in iter_page_files(root):
        meta, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        pages.append(
            {
                "path": path,
                "ref": path.relative_to(wiki_dir(root)).as_posix(),
                "slug": page_slug(path),
                "meta": meta,
                "body": body,
            }
        )
    return pages


def wikilinks(text: str) -> list[str]:
    """Every ``[[target]]`` (or ``[[target|alias]]``) in the text, lowercased."""
    return [target.strip().lower() for target in _WIKILINK_RE.findall(text)]


_WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)")


def summary_of(body: str, limit: int = 160) -> str:
    """One line describing a page: its first prose paragraph, trimmed."""
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ">", "-", "*", "|", "```", "<!--")):
            continue
        line = re.sub(r"\[\[([^\]|]+)(\|[^\]]+)?\]\]", r"\1", line)
        line = re.sub(r"[*_`]", "", line)
        return line[: limit - 1] + "…" if len(line) > limit else line
    return ""


# --- index -----------------------------------------------------------------

_INDEX_LINE_RE = re.compile(r"^\s*[-*]\s*\[\[([^\]|]+)\]\][\s\u2014\u2013:-]*(.*)$")


def parse_index(text: str) -> list[tuple[str, str]]:
    """``- [[slug]] — summary`` lines of an index, as ``(slug, summary)`` pairs."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _INDEX_LINE_RE.match(line)
        if match:
            entries.append((match.group(1).strip().lower(), match.group(2).strip()))
    return entries


def read_index(root: Path) -> list[tuple[str, str]]:
    path = wiki_dir(root) / "index.md"
    if not path.is_file():
        return []
    return parse_index(path.read_text(encoding="utf-8", errors="replace"))


# --- text helpers ----------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, accent-free, punctuation-free — the form used for matching."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


def slugify(value: str, fallback: str = "page") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize(value)).strip("-")
    return slug or fallback


# --- writes ----------------------------------------------------------------


def atomic_write(path: Path, text: str) -> None:
    """Write via ``.tmp`` + ``os.replace``: a reader never sees half a page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def source_hash(text: str) -> str:
    """sha256 of a source: the idempotence key stored in page frontmatter."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def today() -> str:
    return date.today().isoformat()


# --- the single LLM call ---------------------------------------------------


def extract_json(reply: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply.

    Models wrap JSON in prose or fences; that is tolerated. What is not
    tolerated is a reply without an object: the caller asked for JSON, and a
    silent empty result would look like "nothing to do".
    """
    if not reply or not reply.strip():
        raise LLMReplyError("empty reply from the model")
    cleaned = re.sub(r"```[a-zA-Z]*", "", reply)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LLMReplyError("no JSON object in the model reply")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMReplyError(f"invalid JSON in the model reply: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMReplyError("the model reply must be a JSON object")
    return data


def default_llm(prompt: str) -> str:
    """One POST to an OpenAI-compatible endpoint — no agent loop, no retries.

    Defaults to a local Ollama server so the free path needs no configuration.
    Credentials are never written here: a token is read from the environment
    only if it exists, and it is a name, never a value.
    """
    import httpx  # imported here so query/lint work without the HTTP stack

    base_url = os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get(MODEL_ENV, DEFAULT_MODEL)
    headers = {"content-type": "application/json"}
    token = os.environ.get(TOKEN_ENV)
    if token:
        headers["authorization"] = f"Bearer {token}"
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }
    reasoning = os.environ.get(REASONING_ENV, DEFAULT_REASONING).strip()
    if reasoning and reasoning.lower() not in ("default", "omit"):
        payload["reasoning_effort"] = reasoning
    timeout = float(os.environ.get("UHB_LLM_TIMEOUT", str(DEFAULT_TIMEOUT)))
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMReplyError("unexpected response shape from the LLM endpoint") from exc
