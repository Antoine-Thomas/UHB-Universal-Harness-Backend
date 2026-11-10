"""The write gate (Phase 1.4): a mode that cannot write, does not write.

These are integration tests, not unit tests: they drive the real pipeline
(``ingest``, ``lint``, ``guard.commit``) over a real directory tree and compare
the tree before and after. The proof is always the same one — the bytes on disk
are unchanged and the call raised.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from uhb.hub.guard import commit, preflight
from uhb.hub.modes import ModeViolation, UnknownMode, get_mode, parse_mode
from uhb.hub.wiki.scripts import DEFAULT_ROOT, split_frontmatter
from uhb.hub.wiki.scripts.ingest import ingest
from uhb.hub.wiki.scripts.lint import lint

PAGE = {
    "slug": "hub",
    "title": "HUB",
    "tags": ["concept"],
    "links": ["uhb"],
    "body": "# HUB\n\nThe brain. See [[uhb]] and [[wiki-pattern]].",
}


class FakeLLM:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self.replies:
            raise AssertionError("unexpected LLM call")
        return self.replies.pop(0)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository-shaped tree, so the shipped `dev` profile applies as written."""
    root = tmp_path / "repo"
    wiki = root / "uhb" / "hub" / "wiki"
    for rel in ("raw/notes", "wiki/concepts", "wiki/entities", "wiki/procedures", "wiki/references"):
        (wiki / rel).mkdir(parents=True, exist_ok=True)
    for name in ("schema.md", "index.md", "log.md", "contradictions.md"):
        source = DEFAULT_ROOT / "wiki" / name
        (wiki / "wiki" / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return root


def wiki_root(repo: Path) -> Path:
    return repo / "uhb" / "hub" / "wiki"


def add_source(repo: Path, name: str, text: str) -> Path:
    path = wiki_root(repo) / "raw" / "notes" / name
    path.write_text(text, encoding="utf-8")
    return path


def snapshot(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` with its exact bytes — the fail-closed fingerprint."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def reply_for(*pages: dict) -> str:
    return json.dumps({"pages": list(pages)})


def write_page(repo: Path, rel: str, title: str, tags, body: str) -> Path:
    from uhb.hub.wiki.scripts import render_page

    path = wiki_root(repo) / "wiki" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    source = wiki_root(repo) / "raw" / "notes" / f"{Path(rel).stem}.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# source\n", encoding="utf-8")
    meta = {
        "title": title,
        "tags": list(tags),
        "sources": [f"raw/notes/{Path(rel).stem}.md"],
        "created": "2026-11-10",
        "updated": "2026-11-10",
        "contested": False,
        "source_hashes": {f"raw/notes/{Path(rel).stem}.md": "0" * 64},
    }
    path.write_text(render_page(meta, body), encoding="utf-8")
    return path


# --- ingest: read-only modes ----------------------------------------------


def test_chat_cannot_compile_a_source(repo: Path):
    source = add_source(repo, "hub.md", "The HUB compiles sources.")
    fake = FakeLLM(reply_for(PAGE))
    before = snapshot(repo)

    with pytest.raises(ModeViolation) as caught:
        ingest(source, workspace=wiki_root(repo), project=repo, mode="chat", llm=fake)

    assert "read-only" in str(caught.value)
    assert snapshot(repo) == before, "a refused compile must leave every byte in place"
    assert fake.calls, "the refusal happens at the write, after the model has answered"


def test_review_cannot_compile_a_source(repo: Path):
    source = add_source(repo, "hub.md", "The HUB compiles sources.")
    before = snapshot(repo)

    with pytest.raises(ModeViolation):
        ingest(
            source,
            workspace=wiki_root(repo),
            project=repo,
            mode="review",
            llm=FakeLLM(reply_for(PAGE)),
        )

    assert snapshot(repo) == before


def test_dev_compiles_the_wiki(repo: Path):
    source = add_source(repo, "hub.md", "The HUB compiles sources.")

    result = ingest(
        source,
        workspace=wiki_root(repo),
        project=repo,
        mode="dev",
        llm=FakeLLM(reply_for(PAGE)),
    )

    assert result.pages_created == ["concepts/hub.md"]
    page = wiki_root(repo) / "wiki" / "concepts" / "hub.md"
    assert page.is_file()
    assert "[[uhb]]" in page.read_text(encoding="utf-8")
    index = (wiki_root(repo) / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[hub]]" in index and "Total pages: 1" in index
    assert "ingest | raw/notes/hub.md" in (wiki_root(repo) / "wiki" / "log.md").read_text(encoding="utf-8")


def test_a_denied_index_aborts_before_the_first_page_is_written(repo: Path):
    """All-or-nothing: the page is planned first, the index denies, nothing lands."""
    source = add_source(repo, "hub.md", "The HUB compiles sources.")
    partial = parse_mode(
        {
            "name": "partial",
            "kind": "execution",
            "read": ["**"],
            "write": ["**"],
            "deny": ["**/index.md"],
        },
        "partial.json",
    )
    before = snapshot(repo)

    with pytest.raises(ModeViolation) as caught:
        ingest(
            source,
            workspace=wiki_root(repo),
            project=repo,
            mode=partial,
            llm=FakeLLM(reply_for(PAGE)),
        )

    assert "denies" in str(caught.value)
    assert not (wiki_root(repo) / "wiki" / "concepts" / "hub.md").exists()
    assert snapshot(repo) == before


def test_dry_run_reports_the_plan_without_a_writable_mode(repo: Path):
    source = add_source(repo, "hub.md", "The HUB compiles sources.")
    before = snapshot(repo)

    result = ingest(
        source,
        dry_run=True,
        workspace=wiki_root(repo),
        project=repo,
        mode="chat",
        llm=FakeLLM(reply_for(PAGE)),
    )

    assert result.pages_created == ["concepts/hub.md"]
    assert snapshot(repo) == before, "a dry run writes nothing, in any mode"


def test_an_unknown_mode_fails_before_the_model_is_called(repo: Path):
    source = add_source(repo, "hub.md", "The HUB compiles sources.")
    fake = FakeLLM(reply_for(PAGE))

    with pytest.raises(UnknownMode):
        ingest(source, workspace=wiki_root(repo), project=repo, mode="architect", llm=fake)

    assert fake.calls == []
    assert not (wiki_root(repo) / "wiki" / "concepts" / "hub.md").exists()


# --- lint: the registry is a write too ------------------------------------


def _two_pages_sharing_a_tag(repo: Path) -> None:
    write_page(repo, "entities/omniroute.md", "OmniRoute", ["entity"], "# OmniRoute\n\nRuns on port 20128. See [[hub]] and [[proxy]].")
    write_page(repo, "entities/omniroute-legacy.md", "OmniRoute legacy", ["entity"], "# OmniRoute legacy\n\nRuns on port 20129. See [[hub]] and [[proxy]].")
    write_page(repo, "concepts/hub.md", "HUB", ["concept"], "# HUB\n\nSee [[omniroute]] and [[proxy]].")
    write_page(repo, "concepts/proxy.md", "Proxy", ["concept"], "# Proxy\n\nSee [[hub]] and [[omniroute]].")


def _contradiction_llm() -> FakeLLM:
    def answer(_prompt: str) -> str:
        return json.dumps({"contradiction": True, "subject": "port number"})

    class Repeater:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def __call__(self, prompt: str) -> str:
            self.calls.append(prompt)
            return answer(prompt)

    return Repeater()


def test_lint_cannot_update_the_registry_in_a_read_only_mode(repo: Path):
    _two_pages_sharing_a_tag(repo)
    registry = wiki_root(repo) / "wiki" / "contradictions.md"
    before = snapshot(repo)

    with pytest.raises(ModeViolation):
        lint(workspace=wiki_root(repo), project=repo, check_contradictions=True, llm=_contradiction_llm(), mode="chat")

    assert snapshot(repo) == before, "neither the registry nor a page moved"
    assert "port number" not in registry.read_text(encoding="utf-8")
    meta, _ = split_frontmatter((wiki_root(repo) / "wiki" / "entities" / "omniroute.md").read_text(encoding="utf-8"))
    assert meta["contested"] is False


def test_lint_under_dev_records_the_contradiction(repo: Path):
    _two_pages_sharing_a_tag(repo)

    result = lint(
        workspace=wiki_root(repo),
        project=repo,
        check_contradictions=True,
        llm=_contradiction_llm(),
        mode="dev",
    )

    assert result.contradictions[0]["subject"] == "port number"
    assert "- Subject: port number" in (wiki_root(repo) / "wiki" / "contradictions.md").read_text(encoding="utf-8")
    meta, _ = split_frontmatter((wiki_root(repo) / "wiki" / "entities" / "omniroute.md").read_text(encoding="utf-8"))
    assert meta["contested"] is True


# --- the gate itself ------------------------------------------------------


def test_preflight_checks_every_item_before_the_first_write(tmp_path: Path):
    first = tmp_path / "repo" / "a.txt"
    second = tmp_path / "repo" / "b.txt"
    locked = parse_mode(
        {"name": "locked", "kind": "execution", "read": ["**"], "write": ["**"], "deny": ["b.txt"]},
        "locked.json",
    )

    with pytest.raises(ModeViolation):
        commit(locked, [(first, "one"), (second, "two")], project=tmp_path / "repo")

    assert not first.exists(), "the first item must not be written when the second is refused"


def test_preflight_returns_the_paths_it_would_write(tmp_path: Path):
    plan = preflight(get_mode("dev"), [(tmp_path / "uhb" / "x" / "y.md", "text")], project=tmp_path)

    assert [item.relative for item in plan] == ["uhb/x/y.md"]
    assert not (tmp_path / "uhb" / "x" / "y.md").exists()


def test_a_write_outside_the_project_is_refused(repo: Path):
    with pytest.raises(ModeViolation):
        commit(get_mode("dev"), [(repo.parent / "outside.md", "x")], project=repo)
