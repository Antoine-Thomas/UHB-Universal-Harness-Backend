"""HUB wiki (Phase 1.1): three layers, three operations.

Everything but the last test runs against a fake LLM, so the suite is free and
fast. The last test is the real thing — a local Ollama server — and is opt-in:

    UHB_OLLAMA_E2E=1 python -m pytest uhb/tests/test_hub_wiki.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from uhb.hub.wiki.scripts import (
    DEFAULT_ROOT,
    iter_page_files,
    render_page,
    split_frontmatter,
    wikilinks,
)
from uhb.hub.modes import parse_mode
from uhb.hub.wiki.scripts.ingest import ingest, write_index
from uhb.hub.wiki.scripts.lint import lint
from uhb.hub.wiki.scripts.query import query

# A test envelope: full write scope inside the temporary workspace. The gate is
# not loosened for tests — a profile is supplied, and it is data like any other.
WRITER = parse_mode(
    {"name": "test-writer", "kind": "execution", "read": ["**"], "write": ["**"], "deny": ["**/.env"]},
    "test-writer.json",
)


def compile_source(*args, **kwargs):
    kwargs.setdefault("mode", WRITER)
    return ingest(*args, **kwargs)


def run_lint(*args, **kwargs):
    kwargs.setdefault("mode", WRITER)
    return lint(*args, **kwargs)


# --- harness ---------------------------------------------------------------


class FakeLLM:
    """A callable standing in for the model: returns queued replies, counts calls."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.replies:
            raise AssertionError("unexpected LLM call: the queue is empty")
        return self.replies.pop(0)


def reply(*pages: dict) -> str:
    return json.dumps({"pages": list(pages)})


def page_spec(slug, title, body, tags=("concept",), links=()):
    return {"slug": slug, "title": title, "tags": list(tags), "links": list(links), "body": body}


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    """A fresh wiki with the shipped conventions and no pages."""
    root = tmp_path / "hub"
    for rel in ("raw/notes", "wiki/concepts", "wiki/entities", "wiki/procedures", "wiki/references"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    for name in ("schema.md", "index.md", "log.md", "contradictions.md"):
        source = DEFAULT_ROOT / "wiki" / name
        assert source.is_file(), f"shipped convention file missing: {source}"
        (root / "wiki" / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def add_source(root: Path, name: str, text: str) -> Path:
    path = root / "raw" / "notes" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_page(root: Path, rel: str, title: str, tags, body: str, sources=None, contested=False) -> Path:
    """Write a page directly, as a hand-written page or a previous ingest would."""
    sources = list(sources or [f"raw/notes/{Path(rel).stem}.md"])
    for ref in sources:
        target = root / ref
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file():
            target.write_text("# source\n\nnotes\n", encoding="utf-8")
    meta = {
        "title": title,
        "tags": list(tags),
        "sources": sources,
        "created": "2026-11-10",
        "updated": "2026-11-10",
        "contested": contested,
        "source_hashes": {sources[0]: "0" * 64},
    }
    path = root / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_page(meta, body), encoding="utf-8")
    return path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- ingest ----------------------------------------------------------------


def test_ingest_empty_source_is_a_noop(ws: Path):
    source = add_source(ws, "blank.md", "   \n\n\t\n")
    fake = FakeLLM()

    result = compile_source(source, workspace=ws, llm=fake)

    assert result.skipped is True
    assert result.pages_created == [] and result.pages_updated == []
    assert fake.calls == [], "an empty source must not cost a model call"
    assert list((ws / "wiki" / "concepts").glob("*.md")) == []


def test_ingest_creates_pages_with_valid_frontmatter(ws: Path):
    source = add_source(ws, "hub.md", "The HUB compiles sources into cross-linked pages.")
    fake = FakeLLM(
        reply(
            page_spec("hub", "HUB", "# HUB\n\nThe hub compiles sources. See [[wiki-pattern]] and [[ingest]]."),
            page_spec(
                "wiki-pattern",
                "Wiki pattern",
                "# Wiki pattern\n\nRaw becomes wiki, wiki obeys a schema. See [[hub]] and [[ingest]].",
            ),
        )
    )

    result = compile_source(source, workspace=ws, llm=fake)

    assert result.pages_created == ["concepts/hub.md", "concepts/wiki-pattern.md"]
    assert result.skipped is False

    meta, body = split_frontmatter(read(ws / "wiki" / "concepts" / "hub.md"))
    assert meta["title"] == "HUB"
    assert meta["tags"] == ["concept"]
    assert meta["sources"] == ["raw/notes/hub.md"]
    assert meta["contested"] is False
    assert len(meta["source_hashes"]["raw/notes/hub.md"]) == 64
    assert "[[wiki-pattern]]" in body

    index = read(ws / "wiki" / "index.md")
    assert "[[hub]]" in index and "[[wiki-pattern]]" in index
    assert "Total pages: 2" in index

    log = read(ws / "wiki" / "log.md")
    assert "ingest | raw/notes/hub.md" in log
    assert source.name in fake.calls[0], "the prompt must carry the source"


def test_ingest_is_idempotent_on_unchanged_source(ws: Path):
    source = add_source(ws, "hub.md", "The HUB compiles sources.")
    fake = FakeLLM(
        reply(page_spec("hub", "HUB", "# HUB\n\nCompiles. See [[a]] and [[b]].")),
    )
    first = compile_source(source, workspace=ws, llm=fake)
    assert first.pages_created == ["concepts/hub.md"]
    compiled = ws / "wiki" / "concepts" / "hub.md"
    before = compiled.read_bytes()
    stamp = compiled.stat().st_mtime_ns

    second = compile_source(source, workspace=ws, llm=fake)  # queue is empty: a call would raise

    assert second.skipped is True
    assert "unchanged" in second.reason
    assert compiled.read_bytes() == before
    assert compiled.stat().st_mtime_ns == stamp
    assert len(fake.calls) == 1


def test_ingest_modified_source_updates_the_page(ws: Path):
    source = add_source(ws, "hub.md", "The HUB compiles sources.")
    fake = FakeLLM(
        reply(page_spec("hub", "HUB", "# HUB\n\nCompiles. See [[a]] and [[b]].")),
        reply(page_spec("hub", "HUB", "# HUB\n\nCompiles and lints. See [[a]] and [[b]].")),
    )
    compile_source(source, workspace=ws, llm=fake)
    first_hash = split_frontmatter(read(ws / "wiki" / "concepts" / "hub.md"))[0]["source_hashes"][
        "raw/notes/hub.md"
    ]

    source.write_text("The HUB compiles sources and lints them.", encoding="utf-8")
    result = compile_source(source, workspace=ws, llm=fake)

    assert result.pages_created == []
    assert result.pages_updated == ["concepts/hub.md"]
    meta, body = split_frontmatter(read(ws / "wiki" / "concepts" / "hub.md"))
    assert "lints" in body
    assert meta["source_hashes"]["raw/notes/hub.md"] != first_hash
    assert meta["created"] == meta["updated"], "an update keeps the creation date"


def test_ingest_dry_run_writes_nothing(ws: Path):
    source = add_source(ws, "hub.md", "The HUB compiles sources.")
    fake = FakeLLM(reply(page_spec("hub", "HUB", "# HUB\n\nSee [[a]] and [[b]].")))
    before = read(ws / "wiki" / "index.md")

    result = compile_source(source, workspace=ws, llm=fake, dry_run=True)

    assert result.dry_run is True
    assert result.pages_created == ["concepts/hub.md"]
    assert not (ws / "wiki" / "concepts" / "hub.md").exists()
    assert read(ws / "wiki" / "index.md") == before


def test_ingest_refuses_a_source_outside_raw(ws: Path, tmp_path: Path):
    outsider = tmp_path / "elsewhere.md"
    outsider.write_text("not a source of this wiki\n", encoding="utf-8")

    with pytest.raises(Exception) as caught:
        compile_source(outsider, workspace=ws, llm=FakeLLM())

    assert "raw" in str(caught.value)


def test_ingest_rejects_a_non_json_reply(ws: Path):
    source = add_source(ws, "hub.md", "The HUB compiles sources.")

    with pytest.raises(Exception) as caught:
        compile_source(source, workspace=ws, llm=FakeLLM("Sure! Here is a nice page about the hub."))

    assert "JSON" in str(caught.value)
    assert list((ws / "wiki" / "concepts").glob("*.md")) == []


# --- query -----------------------------------------------------------------


def test_query_exact_title_returns_high_confidence(ws: Path):
    write_page(ws, "concepts/hub.md", "HUB", ["concept"], "# HUB\n\nThe brain. See [[uhb]] and [[schema]].")
    write_page(ws, "concepts/uhb.md", "UHB", ["concept"], "# UHB\n\nThe contract. See [[hub]] and [[schema]].")
    write_index(ws, WRITER)

    result = query("HUB", workspace=ws)

    assert result.confidence == "high"
    assert result.pages[0].path == "concepts/hub.md"
    assert result.pages[0].title == "HUB"


def test_query_tag_match_returns_medium_confidence(ws: Path):
    write_page(
        ws,
        "procedures/ingest-flow.md",
        "Ingest flow",
        ["procedure"],
        "# Ingest flow\n\nRead, compile, write. See [[hub]] and [[lint]].",
    )
    write_page(ws, "concepts/hub.md", "HUB", ["concept"], "# HUB\n\nThe brain. See [[ingest-flow]] and [[lint]].")
    write_page(ws, "concepts/lint.md", "Lint", ["concept"], "# Lint\n\nHealth report. See [[hub]] and [[ingest-flow]].")
    write_index(ws, WRITER)

    result = query("procedure", workspace=ws)

    assert result.confidence == "medium"
    assert result.pages[0].path == "procedures/ingest-flow.md"


def test_query_unknown_word_returns_nothing(ws: Path):
    write_page(ws, "concepts/hub.md", "HUB", ["concept"], "# HUB\n\nSee [[uhb]] and [[schema]].")
    write_page(ws, "concepts/uhb.md", "UHB", ["concept"], "# UHB\n\nSee [[hub]] and [[schema]].")
    write_index(ws, WRITER)

    result = query("zebracorn", workspace=ws)

    assert result.pages == []
    assert result.confidence == "low"


def test_query_respects_max_pages(ws: Path):
    names = ["one", "two", "three", "four", "five", "six"]
    for position, name in enumerate(names):
        neighbours = [names[(position + 1) % len(names)], names[(position + 2) % len(names)]]
        write_page(
            ws,
            f"concepts/note-{name}.md",
            f"Note {name}",
            ["reference"],
            f"# Note {name}\n\nA note. See [[note-{neighbours[0]}]] and [[note-{neighbours[1]}]].",
        )
    write_index(ws, WRITER)

    result = query("reference", max_pages=2, workspace=ws)

    assert len(result.pages) == 2
    assert len(query("reference", max_pages=10, workspace=ws).pages) == 6


# --- lint ------------------------------------------------------------------


def test_lint_detects_orphan(ws: Path):
    write_page(ws, "concepts/a.md", "A", ["concept"], "# A\n\nSee [[b]] and [[conc]].")
    write_page(ws, "concepts/b.md", "B", ["concept"], "# B\n\nSee [[a]] and [[conc]].")
    write_page(ws, "concepts/conc.md", "Conc", ["concept"], "# Conc\n\nSee [[a]] and [[b]].")
    write_page(ws, "concepts/lonely.md", "Lonely", ["concept"], "# Lonely\n\nSee [[a]] and [[b]].")
    write_index(ws, WRITER)

    result = run_lint(workspace=ws)

    assert result.orphans == ["concepts/lonely.md"]
    assert result.ok is False


def test_lint_detects_broken_link(ws: Path):
    write_page(ws, "concepts/a.md", "A", ["concept"], "# A\n\nThe missing page. See [[nowhere]] and [[b]].")
    write_page(ws, "concepts/b.md", "B", ["concept"], "# B\n\nSee [[a]] and [[schema]].")
    write_index(ws, WRITER)

    result = run_lint(workspace=ws)

    assert {"page": "concepts/a.md", "target": "nowhere"} in result.broken_links


def test_lint_detects_index_gap(ws: Path):
    write_page(ws, "concepts/a.md", "A", ["concept"], "# A\n\nSee [[b]] and [[schema]].")
    write_page(ws, "concepts/b.md", "B", ["concept"], "# B\n\nSee [[a]] and [[schema]].")
    write_page(ws, "concepts/hidden.md", "Hidden", ["concept"], "# Hidden\n\nSee [[a]] and [[b]].")
    write_index(ws, WRITER)
    # the gap is created after the catalogue was rendered
    write_page(ws, "concepts/late.md", "Late", ["concept"], "# Late\n\nSee [[a]] and [[b]].")

    result = run_lint(workspace=ws)

    assert "concepts/late.md" in result.index_gaps


def test_lint_detects_invalid_frontmatter(ws: Path):
    path = ws / "wiki" / "concepts" / "bad.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                'title: "Bad"',
                "tags: [concept, bogus]",
                'created: "2026-11-10"',
                'updated: "2026-11-10"',
                "contested: false",
                "---",
                "",
                "# Bad",
                "",
                "Links nowhere. See [[a]] and [[b]].",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_page(ws, "concepts/a.md", "A", ["concept"], "# A\n\nSee [[b]] and [[bad]].")
    write_page(ws, "concepts/b.md", "B", ["concept"], "# B\n\nSee [[a]] and [[bad]].")
    write_index(ws, WRITER)

    result = run_lint(workspace=ws)

    reported = " | ".join(
        " | ".join(entry["errors"])
        for entry in result.invalid_frontmatter
        if entry["page"] == "concepts/bad.md"
    )
    assert "tag(s) outside taxonomy: bogus" in reported
    assert "no source" in reported


def test_lint_taxonomy_has_no_false_positive(ws: Path):
    write_page(ws, "concepts/hub.md", "HUB", ["concept"], "# HUB\n\nSee [[uhb]] and [[ingest]].")
    write_page(ws, "entities/uhb.md", "UHB", ["entity"], "# UHB\n\nSee [[hub]] and [[log]].")
    write_page(ws, "procedures/ingest.md", "Ingest", ["procedure"], "# Ingest\n\nSee [[hub]] and [[log]].")
    write_page(ws, "references/log.md", "Log", ["reference"], "# Log\n\nSee [[uhb]] and [[hub]].")
    write_index(ws, WRITER)

    result = run_lint(workspace=ws)

    assert result.invalid_frontmatter == []
    assert result.orphans == []
    assert result.broken_links == []
    assert result.index_gaps == []
    assert result.ok is True


def test_reserved_names_only_exclude_root_files(ws: Path):
    """A page named `log` in a tag dir is a page; only `wiki/log.md` is structure."""
    page = write_page(ws, "references/log.md", "Log", ["reference"], "# Log\n\nSee [[hub]] and [[uhb]].")
    write_page(ws, "concepts/hub.md", "HUB", ["concept"], "# HUB\n\nSee [[log]] and [[uhb]].")
    write_page(ws, "entities/uhb.md", "UHB", ["entity"], "# UHB\n\nSee [[hub]] and [[log]].")
    write_index(ws, WRITER)

    loaded = {path.relative_to(ws / "wiki").as_posix() for path in iter_page_files(ws)}

    assert "references/log.md" in loaded
    assert page.is_file()
    assert "log.md" not in loaded


def test_lint_records_contradictions_and_flags_pages(ws: Path):
    write_page(
        ws,
        "entities/omniroute.md",
        "OmniRoute",
        ["entity"],
        "# OmniRoute\n\nRuns on port 20128. See [[hub]] and [[proxy]].",
    )
    write_page(
        ws,
        "entities/omniroute-legacy.md",
        "OmniRoute legacy",
        ["entity"],
        "# OmniRoute legacy\n\nRuns on port 20129. See [[hub]] and [[proxy]].",
    )
    write_page(ws, "concepts/hub.md", "HUB", ["concept"], "# HUB\n\nSee [[omniroute]] and [[proxy]].")
    write_page(ws, "concepts/proxy.md", "Proxy", ["concept"], "# Proxy\n\nSee [[hub]] and [[omniroute]].")
    write_index(ws, WRITER)

    compared: list[str] = []

    def fake(prompt: str) -> str:
        compared.append(prompt)
        if "port 20129" in prompt:
            return json.dumps({"contradiction": True, "subject": "port number"})
        return json.dumps({"contradiction": False, "subject": ""})

    result = run_lint(workspace=ws, check_contradictions=True, llm=fake)

    assert len(compared) == 2, "one model call per candidate pair"
    assert result.contradictions == [
        {"a": "omniroute", "b": "omniroute-legacy", "subject": "port number"}
    ]
    assert result.ok is False
    registry = read(ws / "wiki" / "contradictions.md")
    assert registry.startswith("# Contradictions")
    assert " vs " in registry
    assert "- Subject: port number" in registry
    assert "- Verdict: pending" in registry

    flagged = split_frontmatter(read(ws / "wiki" / "entities" / "omniroute.md"))[0]
    assert flagged["contested"] is True
    untouched = split_frontmatter(read(ws / "wiki" / "concepts" / "hub.md"))[0]
    assert untouched["contested"] is False


# --- end to end (real model, opt-in) ---------------------------------------


@pytest.mark.skipif(
    os.environ.get("UHB_OLLAMA_E2E") != "1",
    reason="set UHB_OLLAMA_E2E=1 to compile a source against the local Ollama server",
)
def test_end_to_end_with_local_ollama(ws: Path):
    """One real compile: a note in, cross-linked pages out, no mock in between."""
    source = add_source(
        ws,
        "hub-note.md",
        (
            "The HUB is the reference brain of UHB. It keeps three layers: raw sources, "
            "compiled wiki pages, and a schema that binds both. Ingest is a single call to "
            "a model. Lint reports orphans and dead links.\n"
        ),
    )

    result = compile_source(source, workspace=ws)

    assert result.skipped is False, result.reason
    assert result.pages_created or result.pages_updated
    for ref in result.pages_created + result.pages_updated:
        meta, body = split_frontmatter(read(ws / "wiki" / ref))
        assert meta["tags"] and meta["sources"] and meta["source_hashes"]
        assert len(wikilinks(body)) >= 2
    assert run_lint(workspace=ws).index_gaps == []
