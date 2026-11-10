"""Optional services: asset resolution, checksum refusal, installer and launcher.

No network here. The SiYuan release metadata is replaced by a fixture that mirrors the
real `SHA256SUMS.txt` shape, and the one download test goes through a mock transport so
the hash check is exercised for real (a wrong hash must leave nothing behind).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from uhb.entrypoints import cli
from uhb.services import install


# --- platform and asset resolution (verified against SiYuan v3.8.10) --------


def test_platform_keys_cover_the_three_siyuan_families():
    assert install.platform_key("Windows") == "windows"
    assert install.platform_key("Darwin") == "macos"
    assert install.platform_key("Linux") == "linux"


def test_asset_names_match_the_published_ones():
    assert install.asset_name("3.8.10", "windows") == "siyuan-3.8.10-win.exe"
    assert install.asset_name("v3.8.10", "linux") == "siyuan-3.8.10-linux.tar.gz"
    assert install.asset_name("3.8.10", "macos") == "siyuan-3.8.10-mac.dmg"


def test_sha256sums_parsing_tolerates_star_and_noise():
    text = (
        "99a51a9296fe6dfe3a4ec77206d751243ef762170bbd1799726a9a344003dc6d  siyuan-3.8.10-win.exe\n"
        "  ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff *siyuan-3.8.10-linux.tar.gz\n"
        "\n"
        "not a hash  siyuan-whatever\n"
    )
    table = install.parse_sha256sums(text)

    assert table["siyuan-3.8.10-win.exe"].startswith("99a51a92")
    assert "siyuan-3.8.10-linux.tar.gz" in table
    assert "siyuan-whatever" not in table


# --- fail-closed on MARI (no upstream) -------------------------------------


def test_mari_defaults_to_the_bridge_that_ships_here(monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: "dotnet")

    steps = install.plan_mari()

    assert steps[0].service == "mari"
    assert "Bridge.csproj" in " ".join(steps[0].argv)
    assert steps[0].expect is not None and steps[0].expect.name == "Bridge.dll"


def test_mari_without_the_framework_submodule_says_how_to_init_it(tmp_path, monkeypatch):
    monkeypatch.setattr(install.shutil, "which", lambda name: "dotnet")
    monkeypatch.setattr(install, "MARI_FRAMEWORK", tmp_path / "empty")

    with pytest.raises(RuntimeError) as caught:
        install.plan_mari()

    assert "submodule update" in str(caught.value)


def test_mari_with_the_wrong_directory_names_the_missing_project_file(tmp_path):
    with pytest.raises(RuntimeError) as caught:
        install.plan_mari(tmp_path)

    assert "Bridge.csproj" in str(caught.value)


def test_mari_without_the_dotnet_sdk_refuses_before_building(tmp_path, monkeypatch):
    (tmp_path / "Bridge.csproj").write_text("<Project />", encoding="utf-8")
    monkeypatch.setattr(install.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError) as caught:
        install.plan_mari(tmp_path)

    assert "dotnet" in str(caught.value)


# --- download: the hash is the gate ----------------------------------------


def test_a_download_with_the_right_hash_lands(tmp_path, monkeypatch):
    payload = b"x" * 2048
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        install, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=payload))),
    )

    written = install.download("https://example.test/a.bin", tmp_path / "a.bin", digest)

    assert written == 2048
    assert (tmp_path / "a.bin").read_bytes() == payload
    assert not (tmp_path / "a.bin.part").exists()


def test_a_download_with_the_wrong_hash_leaves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        install, "_client",
        lambda: httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"tampered"))),
    )

    with pytest.raises(RuntimeError) as caught:
        install.download("https://example.test/a.bin", tmp_path / "a.bin", "0" * 64)

    assert "checksum mismatch" in str(caught.value)
    assert not (tmp_path / "a.bin").exists()
    assert not (tmp_path / "a.bin.part").exists()


# --- the installer's own command line --------------------------------------


def test_dry_run_resolves_the_release_and_its_published_hash(monkeypatch, capsys):
    name = install.asset_name("3.8.10", install.platform_key())
    monkeypatch.setattr(install, "fetch_latest_version", lambda: "3.8.10")
    monkeypatch.setattr(install, "fetch_checksums", lambda version: {name: "a" * 64})

    code = install.main(["--with", "siyuan", "--dry-run"])

    assert code == 0
    out = capsys.readouterr().out
    assert name in out
    assert "dry-run" in out


def test_an_unknown_service_is_refused():
    assert install.main(["--with", "postgres", "--dry-run"]) == 2


def test_rag_without_yes_stops_before_the_heavy_install(monkeypatch):
    monkeypatch.setattr(install, "fetch_latest_version", lambda: "3.8.10")
    monkeypatch.setattr(install, "fetch_checksums", lambda version: {})

    assert install.main(["--with", "rag"]) == 2


def test_json_mode_is_machine_readable(monkeypatch, capsys):
    name = install.asset_name("3.8.10", install.platform_key())
    monkeypatch.setattr(install, "fetch_latest_version", lambda: "3.8.10")
    monkeypatch.setattr(install, "fetch_checksums", lambda version: {name: "b" * 64})

    assert install.main(["--with", "siyuan", "--dry-run", "--json"]) == 0
    steps = json.loads(capsys.readouterr().out)

    assert steps[0]["service"] == "siyuan"
    assert steps[0]["sha256"] == "b" * 64
    assert steps[0]["url"].endswith(name)


# --- docker compose is part of the deliverable -----------------------------


def test_the_compose_stack_declares_the_three_services():
    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[2]
    stack = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))

    assert set(stack["services"]) == {"uhb", "rag", "siyuan"}
    assert stack["services"]["siyuan"]["command"][0] == "serve", \
        "SiYuan 3.8.x refuses bare flags: the subcommand is required"
    for service in stack["services"].values():
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:"), "a local stack must not listen on the LAN"


# --- `uhb brain start` -----------------------------------------------------


def test_brain_start_says_what_is_missing_instead_of_crashing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)

    code = cli.main(["brain", "start", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 1
    assert "skip  siyuan" in out and "install_services" in out
    assert "nothing to start" in out


def test_brain_start_prints_the_command_it_would_run(tmp_path, monkeypatch, capsys):
    (tmp_path / "siyuan").mkdir()
    (tmp_path / "siyuan" / "siyuan-3.8.10-win.exe").write_bytes(b"stub")
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)

    code = cli.main(["brain", "start", "--only", "siyuan", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert "siyuan-3.8.10-win.exe" in out
    assert "127.0.0.1:6806" in out
    assert "dry-run" in out


# --- monorepo completeness -------------------------------------------------


def test_the_repository_carries_the_sources_it_claims_to_carry():
    root = Path(__file__).resolve().parents[2]

    assert (root / "uhb" / "services" / "rag" / "serveur_rag.py").is_file()
    assert (root / "uhb" / "services" / "rag" / "chercher.py").is_file()
    assert (root / "uhb" / "services" / "mari" / "bridge" / "Bridge.csproj").is_file()
    assert (root / "uhb" / "services" / "mari" / "bridge" / "Program.cs").is_file()
    assert (root / "docker-compose.yml").is_file()
    assert (root / "install_services.sh").is_file()
    assert (root / "install_services.ps1").is_file()


def test_the_mari_framework_is_a_pinned_submodule_not_a_copied_tree():
    root = Path(__file__).resolve().parents[2]
    # .gitmodules is written with tabs; YAML forbids them as indentation, so the
    # file is checked as text rather than parsed.
    text = (root / ".gitmodules").read_text(encoding="utf-8").replace(chr(9), " ")

    assert '[submodule "uhb/services/mari/framework"]' in text
    assert "url = https://github.com/Antoine-Thomas/mari.git" in text
    assert "path = uhb/services/mari/framework" in text
    framework = root / "uhb" / "services" / "mari" / "framework"
    assert (framework / "LICENSE").is_file() or not any(framework.iterdir()), \
        "an initialised submodule carries its own MIT licence"


def test_the_bridge_references_the_submodule_with_portable_separators():
    root = Path(__file__).resolve().parents[2]
    project = (root / "uhb" / "services" / "mari" / "bridge" / "Bridge.csproj").read_text(
        encoding="utf-8")

    assert "../framework/src/Frameworks/" in project, "MSBuild accepts '/' on every OS"
    assert ".." + chr(92) + "mari" + chr(92) + "src" not in project, \
        "the old sibling path must be gone"
    allowed = {chr(10), chr(13), chr(9)}
    assert all(ord(char) >= 0x20 or char in allowed for char in project), \
        "no form feed or other control character in the XML"


def test_brain_start_aliases_point_at_the_right_service(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(install, "BIN_DIR", tmp_path)

    code = cli.main(["brain", "start-mari", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 1
    assert "skip  mari" in out


def test_the_packaging_rules_cannot_drag_the_submodule_into_the_wheel():
    root = Path(__file__).resolve().parents[2]
    project = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert "namespaces = false" in project,         "namespace discovery would treat the MARI submodule as a package"
    assert "include-package-data = false" in project,         "the default walks package dirs and ships third-party .py files"
    assert "services/mari/bridge/*.cs" in project, "the bridge source must ship"
