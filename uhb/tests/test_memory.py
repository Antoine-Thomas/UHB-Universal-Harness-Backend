"""sync_memory: three levels, three harness conventions, idempotent by content."""

from __future__ import annotations

import pytest

from uhb.hub.modes import ModeRequired, ModeViolation, parse_mode

from uhb.core.memory import MemorySync, UnknownLevel

# A test envelope: full write scope inside the temporary workspace. The gate is
# not loosened for tests — a profile is supplied, and it is data like any other.
WRITER = parse_mode(
    {"name": "test-writer", "kind": "execution", "read": ["**"], "write": ["**"], "deny": ["**/.env"]},
    "test-writer.json",
)


def sync_memory_for(workspace, **kwargs):
    kwargs.setdefault("mode", WRITER)
    return MemorySync(workspace, **kwargs)




@pytest.fixture()
def workspace(tmp_path):
    return tmp_path


def _read(path):
    return path.read_text(encoding="utf-8")


def test_workspace_level_writes_at_root(workspace):
    result = sync_memory_for(workspace, harness="hermes").sync_memory("workspace", "decision A")
    assert result.synced is True
    assert result.path.endswith("MEMORY.md")
    assert "decision A" in _read(workspace / "MEMORY.md")


def test_project_level_writes_under_projects(workspace):
    result = sync_memory_for(workspace, harness="hermes", project="acme").sync_memory("project", "decision B")
    assert result.path.endswith("projects/acme/MEMORY.md") or "projects" in result.path
    assert (workspace / "projects" / "acme" / "MEMORY.md").is_file()


def test_topic_level_writes_under_topic(workspace):
    sync = sync_memory_for(workspace, harness="hermes", project="acme", topic="api")
    result = sync.sync_memory("topic", "decision C")
    expected = workspace / "projects" / "acme" / "topics" / "api" / "MEMORY.md"
    assert expected.is_file()
    assert result.level == "topic"


def test_second_write_of_same_content_is_a_no_op(workspace):
    sync = sync_memory_for(workspace, harness="hermes")
    first = sync.sync_memory("workspace", "same block")
    second = sync.sync_memory("workspace", "same block")
    assert first.synced is True
    assert second.synced is False
    assert _read(workspace / "MEMORY.md").count("same block") == 1


def test_two_different_blocks_coexist(workspace):
    sync = sync_memory_for(workspace, harness="hermes")
    sync.sync_memory("workspace", "block one")
    sync.sync_memory("workspace", "block two")
    text = _read(workspace / "MEMORY.md")
    assert "block one" in text and "block two" in text


def test_each_block_carries_a_content_hash(workspace):
    sync_memory_for(workspace, harness="hermes").sync_memory("workspace", "hashed")
    assert "<!-- uhb:" in _read(workspace / "MEMORY.md")


def test_unknown_level_raises(workspace):
    with pytest.raises(UnknownLevel):
        sync_memory_for(workspace).sync_memory("galaxy", "x")


@pytest.mark.parametrize(
    "harness,filename",
    [("claude", "CLAUDE.md"), ("harness-maxxing", "CLAUDE.md"),
     ("openclaw", "memory.md"), ("hermes", "MEMORY.md")],
)
def test_harness_conventions_pick_the_right_filename(workspace, harness, filename):
    result = sync_memory_for(workspace, harness=harness).sync_memory("workspace", "x")
    assert result.path.endswith(filename)


def test_unknown_harness_falls_back_to_hermes_convention(workspace):
    assert sync_memory_for(workspace, harness="unknown").filename == "MEMORY.md"


def test_levels_do_not_bleed_into_each_other(workspace):
    sync = sync_memory_for(workspace, harness="claude", project="acme", topic="api")
    sync.sync_memory("project", "project only")
    sync.sync_memory("topic", "topic only")
    project_text = _read(workspace / "projects" / "acme" / "CLAUDE.md")
    topic_text = _read(workspace / "projects" / "acme" / "topics" / "api" / "CLAUDE.md")
    assert "topic only" not in project_text
    assert "project only" not in topic_text


def test_memory_sync_without_a_mode_fails_closed(tmp_path):
    with pytest.raises(ModeRequired) as caught:
        MemorySync(tmp_path, harness="hermes").sync_memory("workspace", "no envelope")

    assert "MemorySync needs a mode" in str(caught.value)
    assert not (tmp_path / "MEMORY.md").exists()


def test_memory_sync_under_a_read_only_mode_writes_nothing(tmp_path):
    with pytest.raises(ModeViolation) as caught:
        MemorySync(tmp_path, harness="hermes", mode="review").sync_memory("workspace", "denied")

    assert "read-only" in str(caught.value)
    assert not (tmp_path / "MEMORY.md").exists()
