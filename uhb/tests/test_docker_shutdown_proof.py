"""P1-6 — the Docker shutdown, proved on a real stack.

Opt-in: never runs in CI, never on a machine by accident.

    UHB_DOCKER_E2E=1 python -m pytest uhb/tests/test_docker_shutdown_proof.py -s -q

The `docker` target is only meaningful from a HOST instance of UHB: a server
running inside a container cannot reach the Docker socket, and `shutdown.py`
refuses the target explicitly (`in_container()` keeps `self` only). So the proof
does what an operator does:

1. brings the project's containers up (`--no-deps uhb`: ports 8200 and 6806
   belong to the operator's own RAG and SiYuan on the reference machine);
2. starts a HOST `uhb run` on a free port, with the same shutdown token;
3. asks *that* server for ``{"confirm": true, "stop": ["docker"]}``;
4. checks the containers and the network are **removed** — `down`, never `stop`;
5. checks the host server is **still answering**: `stop` is honoured and `self`
   was not in the list, so the two targets really are separate;
6. checks nothing was destroyed: no `-v` (the set of Docker volumes is identical
   before and after), and a bind-mounted marker file survives. This compose file
   declares no named volume — it mounts `./uhb/data/siyuan` and
   `./uhb/services/rag` — so "volumes intact" is measured on what actually holds
   data here.

The three-service stack and the `rag`/`siyuan` containers are covered by
`docs/proof/docker-compose-e2e.md`; what was missing was the route's `docker`
target, and that is what this file pins.

No `sleep`: readiness is an HTTP probe on `/status`, and every wait polls.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from uhb.entrypoints import shutdown as shutdown_routes

pytestmark = pytest.mark.skipif(
    os.environ.get("UHB_DOCKER_E2E") != "1",
    reason="real stack: set UHB_DOCKER_E2E=1 to run it (Docker, ports, ~60 s)",
)

ROOT = Path(__file__).resolve().parents[2]
MARKER = ROOT / "uhb" / "data" / "siyuan" / "proof-marker.txt"
#: Where the containerised UHB publishes.
CONTAINER_PORT = 8101
#: The project name is fixed, never inferred from the directory: `compose ps` in a
#: tree whose folder was renamed would answer "empty" about another project, and
#: every assertion below would pass while measuring nothing.
PROJECT = "uhb"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def run(
    *argv: str, timeout: float = 120.0, env_extra: dict | None = None, quiet: bool = False
) -> subprocess.CompletedProcess:
    """One command, printed, so the proof can be read as a transcript.

    ``env_extra`` is the only path a secret takes in this file: it reaches the
    child's environment, never argv, and ``run`` prints argv — so a token passed
    this way cannot land in the transcript. ``quiet`` is for the polling loops:
    nineteen identical `compose ps` lines bury the lines that matter.
    """
    completed = subprocess.run(
        list(argv), cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, check=False,
        env={**os.environ, **(env_extra or {})},
    )
    if quiet:
        return completed
    print(f"$ {' '.join(argv)}")
    for stream in (completed.stdout.strip(), completed.stderr.strip()):
        if stream:
            print("  " + stream.replace("\n", "\n  "))
    return completed


def compose(*argv: str, **kwargs) -> subprocess.CompletedProcess:
    """`docker compose -p uhb …`: one project, named, whether we started it or not."""
    return run("docker", "compose", "-p", PROJECT, *argv, **kwargs)


def containers(quiet: bool = False) -> list[str]:
    return [line for line in compose("ps", "-a", "-q", quiet=quiet).stdout.split() if line]


def containers_by_label() -> list[str]:
    """Independent of the compose file: what Docker itself labels with our project."""
    out = run("docker", "ps", "-aq",
              "--filter", f"label=com.docker.compose.project={PROJECT}", quiet=True).stdout
    return [line for line in out.split() if line]


def project_volumes() -> list[str]:
    """Named volumes this project declares. Empty today: it mounts two directories."""
    out = run("docker", "volume", "ls", "-q",
              "--filter", f"label=com.docker.compose.project={PROJECT}", quiet=True).stdout
    return [line for line in out.split() if line]


def volume_exists(name: str) -> bool:
    return run("docker", "volume", "inspect", name, quiet=True).returncode == 0


def networks() -> set[str]:
    return set(run("docker", "network", "ls", "--format", "{{.Name}}").stdout.split())


def volumes() -> set[str]:
    return set(run("docker", "volume", "ls", "--format", "{{.Name}}").stdout.split())


def port_open(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.4)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def status_code(port: int, timeout: float = 2.0) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=timeout) as response:
            return response.status
    except (urllib.error.URLError, OSError):
        return 0


def wait_for(predicate, *, timeout: float = 60.0, interval: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def shutdown_request(port: int, targets: list[str], *, token: str) -> tuple[int, dict]:
    """POST /shutdown, and return the answer. The token is never printed."""
    body = json.dumps({"confirm": True, "stop": targets}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/shutdown",
        data=body,
        headers={"content-type": "application/json", shutdown_routes.TOKEN_HEADER: token},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


@pytest.fixture()
def project():
    """A snapshot to compare against, and a teardown that always runs."""
    before_networks, before_volumes = networks(), volumes()
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text("survives a down\n", encoding="utf-8")
    yield {"networks": before_networks, "volumes": before_volumes}
    # Never `-v`: this is the teardown of the proof, and the proof is about data
    # surviving a `down`. A `-v` here would destroy the very thing being measured.
    compose("down", "--timeout", "10")


@pytest.fixture()
def host_uhb():
    """A host UHB: the only instance whose `docker` target can mean anything."""
    port = free_port()
    token = shutdown_routes.resolve_token(create=True)
    env = {**os.environ, shutdown_routes.TOKEN_ENV: token}
    process = subprocess.Popen(
        [sys.executable, "-m", "uhb.entrypoints.cli", "run", "--mode", "dev", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        assert wait_for(lambda: status_code(port) == 200, timeout=60), (
            "the host UHB never answered /status"
        )
        yield port, token
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - stubborn child
                process.kill()


def test_the_docker_target_removes_the_project_and_keeps_the_data(project, host_uhb) -> None:
    host_port, token = host_uhb
    assert containers() == [], "the proof needs a clean project: `docker compose down` first"
    print(f"AVANT: conteneurs={containers()} port 8101 ecoute={port_open(CONTAINER_PORT)}")

    # 1. the project's containers, built from the current tree: a stale image
    #    answers 404 on /shutdown and made the route look broken (measured).
    compose("build", "uhb", timeout=600)
    compose("up", "-d", "--no-deps", "uhb")
    assert containers(), "the container did not start"
    assert wait_for(lambda: status_code(CONTAINER_PORT) == 200, timeout=90), (
        "the container never answered /status"
    )
    print("READY: conteneur en place, /status 200")

    # 2. the docker target, from the host instance.
    status, answer = shutdown_request(host_port, ["docker"], token=token)
    assert status == 202, f"the route refused with {status}: {answer}"
    assert answer.get("plan", {}).get("targets") == ["docker"], answer

    # 3. removed, not stopped — measured twice: through compose, and through the
    #    Docker label, which does not depend on this compose file being found.
    assert wait_for(lambda: not containers(quiet=True), timeout=90), "containers survived the shutdown"
    assert containers_by_label() == [], (
        "docker ps still lists containers labelled with this project: `compose ps` alone "
        "would have missed them if the project name had drifted"
    )
    assert wait_for(lambda: not port_open(CONTAINER_PORT), timeout=30), (
        "port 8101 is still listened on"
    )
    print("APRES: aucun conteneur, port 8101 libre")

    # 4. the project's network is gone; the operator's others are untouched.
    after_networks = networks()
    assert "uhb_default" not in after_networks, after_networks
    assert project["networks"] <= after_networks, "an unrelated network disappeared"

    # 5. `stop: ["docker"]` did not stop the server that served it.
    assert status_code(host_port) == 200, (
        "the host UHB stopped itself: `self` was not in the target list"
    )
    print("APRES: le serveur hote repond toujours (cible self exclue)")

    # 6. nothing was destroyed: no -v, no volume created or removed, marker intact.
    assert volumes() == project["volumes"], (
        "a volume appeared or vanished: the shutdown must never pass -v\n"
        f"before={sorted(project['volumes'])} after={sorted(volumes())}"
    )
    assert MARKER.read_text(encoding="utf-8").strip() == "survives a down", (
        "the bind mount did not survive: this is the data a real operator loses"
    )
    # A named volume, if this project ever declares one, must still be inspectable:
    # `down` without `-v` preserves it, and `volume inspect` is the check that says so.
    for name in project_volumes():
        assert volume_exists(name), f"volume {name} disappeared: was -v passed?"
    print("APRES: volumes inchanges, marqueur du bind mount intact")
