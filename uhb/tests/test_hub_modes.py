"""Execution modes (Phase 1.2): permissions, prompt envelope, extensibility.

Every test runs against the shipped profiles or a throwaway profile directory —
no network, no workspace mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from uhb.hub.modes import (
    InvalidModeConfig,
    ModeSpec,
    ModeViolation,
    UnknownMode,
    clear_cache,
    get_mode,
    load_modes,
    names,
    parse_mode,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    clear_cache()
    yield
    clear_cache()


def write_profile(directory: Path, name: str, **overrides) -> Path:
    profile = {
        "name": name,
        "description": "test profile",
        "kind": "execution",
        "read": ["**"],
        "write": [],
        "deny": [],
        "tools": ["read_file"],
        "system_prompt": "preamble",
        "constraints": ["a constraint"],
        "model": {"tier": "eco", "temperature": 0.0, "max_tokens": 512},
        "allow_paid": False,
        "max_turns": 1,
    }
    profile.update(overrides)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path


# --- registry --------------------------------------------------------------


def test_registry_lists_the_four_shipped_modes():
    assert names() == ["chat", "dev", "observe", "review"]


def test_profile_is_read_as_data_not_code():
    dev = get_mode("dev")

    assert isinstance(dev, ModeSpec)
    assert dev.tier == "eco"
    assert dev.max_turns == 30
    assert "patch" in dev.tools
    assert dev.allow_paid is False
    assert dev.to_dict()["model"] == {"tier": "eco", "temperature": 0.1, "max_tokens": 8192}


def test_unknown_mode_lists_the_known_ones():
    with pytest.raises(UnknownMode) as caught:
        get_mode("architect")

    assert "chat" in str(caught.value) and "review" in str(caught.value)


def test_a_new_mode_is_one_json_file(tmp_path: Path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    write_profile(directory, "audit", read=["**"], write=["reports/**"], tools=["read_file", "search"])

    spec = get_mode("audit", directory)

    assert spec.name == "audit"
    assert spec.allows_write("reports/2026-11-10.md") is True
    assert spec.allows_write("uhb/core/router.py") is False
    assert "audit" in names(directory)
    assert "dev" in names(directory), "a user directory overlays, it does not replace"


def test_a_user_profile_overrides_a_shipped_one(tmp_path: Path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    write_profile(directory, "chat", write=["notes/**"], tools=["read_file", "write_file"])

    spec = get_mode("chat", directory)

    assert spec.write == ("notes/**",)
    assert spec.allows_write("notes/todo.md") is True


def test_permissions_do_not_silently_change_under_a_running_process(tmp_path: Path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    path = write_profile(directory, "audit", max_turns=1)
    assert get_mode("audit", directory).max_turns == 1

    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "max_turns": 7}), encoding="utf-8")

    assert get_mode("audit", directory).max_turns == 1, "a running process keeps its envelope"
    assert load_modes(directory, refresh=True)["audit"].max_turns == 7


def test_an_unreadable_profile_fails_loudly(tmp_path: Path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(InvalidModeConfig) as caught:
        load_modes(directory)

    assert "broken.json" in str(caught.value)


# --- validation ------------------------------------------------------------


def test_unknown_key_is_refused_naming_the_key():
    with pytest.raises(InvalidModeConfig) as caught:
        parse_mode({"name": "x", "persmissions": ["**"]}, "x.json")

    assert "persmissions" in str(caught.value)


def test_bad_kind_and_bad_tier_are_refused():
    with pytest.raises(InvalidModeConfig) as kind:
        parse_mode({"name": "x", "kind": "sandbox"}, "x.json")
    with pytest.raises(InvalidModeConfig) as tier:
        parse_mode({"name": "x", "model": {"tier": "platinum"}}, "x.json")

    assert "execution" in str(kind.value)
    assert "paid" in str(tier.value)


def test_absolute_pattern_is_refused():
    with pytest.raises(InvalidModeConfig) as caught:
        parse_mode({"name": "x", "read": ["/etc/**"]}, "x.json")

    assert "workspace-relative" in str(caught.value)


def test_climbing_pattern_is_refused():
    with pytest.raises(InvalidModeConfig) as caught:
        parse_mode({"name": "x", "write": ["../outside/**"]}, "x.json")

    assert "climb" in str(caught.value)


# --- permissions -----------------------------------------------------------


def test_dev_writes_project_files_but_not_configuration():
    dev = get_mode("dev")

    assert dev.allows_write("uhb/hub/wiki/scripts/ingest.py") is True
    assert dev.allows_write("tests/test_hub_wiki.py") is True
    assert dev.allows_write("config.yaml") is False
    assert dev.allows_write("skills/repo-hygiene/SKILL.md") is False


def test_deny_wins_over_allow():
    dev = get_mode("dev")
    chat = get_mode("chat")

    assert dev.allows_write("uhb/secrets/token.json") is False
    assert dev.allows_read("uhb/.env") is False
    assert chat.allows_read("wiki/index.md") is True
    assert chat.allows_read("wiki/.env") is False


def test_chat_is_read_only():
    chat = get_mode("chat")

    with pytest.raises(ModeViolation) as caught:
        chat.check_write("wiki/concepts/hub.md")

    assert "read-only" in str(caught.value)


def test_write_outside_scope_is_refused_before_any_write():
    dev = get_mode("dev")

    with pytest.raises(ModeViolation) as caught:
        dev.check_write("production/deploy.sh")

    assert "may not write" in str(caught.value)


def test_traversal_is_refused():
    dev = get_mode("dev")

    with pytest.raises(ModeViolation):
        dev.check_write("../elsewhere/file.md")


def test_absolute_path_is_judged_against_the_workspace(tmp_path: Path):
    dev = get_mode("dev")
    inside = tmp_path / "uhb" / "a.py"
    inside.parent.mkdir()
    inside.write_text("x = 1\n", encoding="utf-8")

    assert dev.allows_write(str(inside), workspace=tmp_path) is True
    with pytest.raises(ModeViolation):
        dev.allows_write("/etc/shadow", workspace=tmp_path)
    with pytest.raises(ModeViolation):
        dev.allows_read("/etc/shadow")  # absolute path, no workspace to judge it


def test_tool_must_be_listed():
    chat = get_mode("chat")

    assert chat.allows_tool("read_file") is True
    with pytest.raises(ModeViolation) as caught:
        chat.check_tool("run_tests")

    assert "no tool" in str(caught.value)


# --- prompt ----------------------------------------------------------------


def test_system_prompt_carries_preamble_and_constraints():
    dev = get_mode("dev")
    prompt = dev.system_prompt

    assert prompt.startswith("You change code")
    assert "Constraints:" in prompt
    assert prompt.count("\n- ") == len(dev.constraints)
    assert ".env" in prompt


def test_prompt_is_deterministic():
    assert get_mode("review").system_prompt == get_mode("review").system_prompt
