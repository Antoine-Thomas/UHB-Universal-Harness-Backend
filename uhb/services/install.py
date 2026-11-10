"""Bootstrap the optional services — one implementation, two thin wrappers.

The point of this module is *durability*: the repository carries the code and the
recipe, never the binaries. A fresh clone can install everything it needs, on any
machine, and the result lands in ``uhb/bin/`` (not versioned).

    python -m uhb.services.install --with siyuan --dry-run
    python -m uhb.services.install --with siyuan
    python -m uhb.services.install --with rag --yes
    python -m uhb.services.install --with mari --mari-src <path to the .NET project>

Everything is fail-closed: a missing checksum, a wrong hash, a missing SDK or a missing
source directory is an error with a message that says what to install — never a silent
half-install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as platform_module
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Where the downloaded / built services land. Never committed, never packaged.
BIN_DIR = Path(__file__).resolve().parents[1] / "bin"

SIYUAN_REPO = "siyuan-note/siyuan"
SIYUAN_RELEASES = f"https://api.github.com/repos/{SIYUAN_REPO}/releases/latest"
SIYUAN_DOWNLOAD = f"https://github.com/{SIYUAN_REPO}/releases/download"

RAG_REQUIREMENTS = Path(__file__).resolve().parent / "rag" / "requirements.txt"

#: The MARI bridge ships with the repository (your code); its framework is a
#: submodule (MIT, fork of kaonomi-net/mari) and must be initialised once.
MARI_BRIDGE = Path(__file__).resolve().parent / "mari" / "bridge"
MARI_FRAMEWORK = Path(__file__).resolve().parent / "mari" / "framework"
RAG_SIZE_HINT = "~1.5 GB (torch CPU, faiss, flashrank, then the e5 model on first use)"

SERVICES = ("siyuan", "rag", "mari")


@dataclass(frozen=True, slots=True)
class Step:
    """One install action, described well enough to be printed instead of run."""

    service: str
    action: str
    detail: str
    argv: tuple[str, ...] = ()
    url: str = ""
    dest: Path | None = None
    sha256: str = ""
    expect: Path | None = None

    def render(self) -> str:
        return f"  [{self.service}] {self.action:10} {self.detail}"


def platform_key(system: str | None = None) -> str:
    """``windows`` / ``linux`` / ``macos`` — the three families SiYuan ships."""
    raw = (system or platform_module.system()).lower()
    if raw.startswith("win"):
        return "windows"
    if raw.startswith("darwin") or raw.startswith("mac"):
        return "macos"
    return "linux"


def asset_suffix(key: str) -> str:
    """The suffix SiYuan uses for that platform. Verified against v3.8.10 assets."""
    return {"windows": "win.exe", "linux": "linux.tar.gz", "macos": "mac.dmg"}[key]


def asset_name(version: str, key: str) -> str:
    return f"siyuan-{version.lstrip('v')}-{asset_suffix(key)}"


def parse_sha256sums(text: str) -> dict[str, str]:
    """``SHA256SUMS.txt`` -> ``{filename: hash}``. Tolerant on ``*`` and spacing."""
    table: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts[0].strip().lower(), parts[1].lstrip("*").strip()
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            table[name] = digest
    return table


def _client():
    import httpx

    return httpx.Client(timeout=30.0, follow_redirects=True)


def fetch_latest_version() -> str:
    """The release tag, without the leading ``v``."""
    with _client() as client:
        data = client.get(SIYUAN_RELEASES).json()
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        raise RuntimeError("siyuan: this GitHub API answer carries no tag_name")
    return tag.lstrip("v")


def fetch_checksums(version: str) -> dict[str, str]:
    """The published ``SHA256SUMS.txt`` for that release. Required, not optional."""
    url = f"{SIYUAN_DOWNLOAD}/v{version.lstrip('v')}/SHA256SUMS.txt"
    with _client() as client:
        response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"siyuan: cannot read {url} (HTTP {response.status_code})")
    table = parse_sha256sums(response.text)
    if not table:
        raise RuntimeError(f"siyuan: {url} parsed to nothing — refusing to install unverified")
    return table


def plan_rag() -> list[Step]:
    return [
        Step("rag", "pip", f"-r {RAG_REQUIREMENTS.name} ({RAG_SIZE_HINT})",
             argv=(sys.executable, "-m", "pip", "install", "-r", str(RAG_REQUIREMENTS))),
    ]


def plan_mari(source: Path | None = None) -> list[Step]:
    """Build the bridge. Defaults to the copy that ships here: no download involved."""
    project = source or MARI_BRIDGE
    if not (project / "Bridge.csproj").is_file():
        raise RuntimeError(f"mari: {project} has no Bridge.csproj — wrong directory?")
    if source is None and not (MARI_FRAMEWORK / "src" / "Frameworks").is_dir():
        raise RuntimeError(
            "mari: the framework submodule is empty — run `git submodule update --init` "
            "from the repository root, then retry (the framework is MIT, forked from "
            "kaonomi-net/mari)"
        )
    if shutil.which("dotnet") is None:
        raise RuntimeError("mari: the .NET SDK is not on PATH (install dotnet 8 or newer)")
    out = BIN_DIR / "mari"
    return [
        Step("mari", "build", f"{project} -> {out}",
             argv=("dotnet", "build", str(project / "Bridge.csproj"), "-c", "Release",
                   "-o", str(out)),
             expect=out / "Bridge.dll"),
    ]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path, expected: str, *, chunk: int = 1 << 20) -> int:
    """Stream to ``dest``, then verify. A hash mismatch removes the file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    total = 0
    with _client() as client, client.stream("GET", url) as response:
        if response.status_code != 200:
            raise RuntimeError(f"download failed: HTTP {response.status_code} for {url}")
        with partial.open("wb") as handle:
            for block in response.iter_bytes(chunk):
                handle.write(block)
                total += len(block)
    actual = sha256_of(partial)
    if expected and actual != expected:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"checksum mismatch for {dest.name}: expected {expected}, got {actual}")
    partial.replace(dest)
    return total


def run_steps(steps: list[Step]) -> int:
    for step in steps:
        if step.argv:
            print(f"  -> {' '.join(step.argv)}")
            completed = subprocess.run(list(step.argv))
            if completed.returncode != 0:
                raise RuntimeError(f"{step.service}: command failed ({completed.returncode})")
        elif step.url and step.dest:
            print(f"  -> {step.url}")
            size = download(step.url, step.dest, step.sha256)
            print(f"     {size / 1e6:.1f} Mo, sha256 {'verified' if step.sha256 else 'NOT verified'}")
        if step.expect is not None and not step.expect.exists():
            raise RuntimeError(
                f"{step.service}: build finished but {step.expect.name} is missing in "
                f"{step.expect.parent} — the step did not produce what it claims"
            )
    return 0


def build_plan(
    wanted: list[str], *, version: str | None, mari_src: Path | None
) -> list[Step]:
    """Resolve the concrete steps. Network is touched only for SiYuan metadata."""
    steps: list[Step] = []
    if "siyuan" in wanted:
        key = platform_key()
        resolved = version or fetch_latest_version()
        checksums = fetch_checksums(resolved)
        name = asset_name(resolved, key)
        if name not in checksums:
            raise RuntimeError(
                f"siyuan: {name} is absent from the release checksums "
                f"(available: {', '.join(sorted(checksums)[:4])}...)"
            )
        steps.append(
            Step("siyuan", "download", f"{name} -> {BIN_DIR / 'siyuan'}",
                 url=f"{SIYUAN_DOWNLOAD}/v{resolved}/{name}",
                 dest=BIN_DIR / "siyuan" / name, sha256=checksums[name])
        )
    if "rag" in wanted:
        steps.extend(plan_rag())
    if "mari" in wanted:
        steps.extend(plan_mari(mari_src))
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uhb-services",
        description="install the optional UHB services (nothing is committed to git)",
    )
    parser.add_argument("--with", dest="services", default="siyuan",
                        help="comma-separated: siyuan,rag,mari (default: siyuan)")
    parser.add_argument("--siyuan-version", default=None, help="pin a SiYuan release")
    parser.add_argument("--mari-src", default=None, help="path of the MARI .NET project")
    parser.add_argument("--dry-run", action="store_true", help="print the steps, change nothing")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--yes", action="store_true",
                        help="do not stop before the heavy step (rag installs ~1.5 GB)")
    args = parser.parse_args(argv)

    wanted = [name.strip().lower() for name in args.services.split(",") if name.strip()]
    unknown = [name for name in wanted if name not in SERVICES]
    if unknown:
        print(f"unknown service(s): {', '.join(unknown)} — choose from {', '.join(SERVICES)}",
              file=sys.stderr)
        return 2
    try:
        steps = build_plan(wanted, version=args.siyuan_version,
                           mari_src=Path(args.mari_src) if args.mari_src else None)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(
            [{"service": s.service, "action": s.action, "url": s.url,
              "dest": str(s.dest) if s.dest else "", "sha256": s.sha256} for s in steps],
            indent=2,
        ))
        return 0

    print(f"UHB services -> {BIN_DIR}")
    for step in steps:
        print(step.render())
    if args.dry_run:
        print("\ndry-run: nothing downloaded, nothing installed.")
        return 0

    if "rag" in wanted and not args.yes:
        print(f"\nrag will install {RAG_SIZE_HINT}.\nRe-run with --yes to accept.", file=sys.stderr)
        return 2

    try:
        return run_steps(steps)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
