"""Machine profile, model catalogue and recommendation.

Three questions, three answers, and none of them is guessed:

* **What is this machine?** :func:`measure_profile` asks the operating system and
  the GPU driver. Anything it cannot measure is ``null`` and named in ``unknown``
  — a machine whose VRAM is unknown is not a machine with 24 GB of VRAM.
* **What could run here?** :func:`recommend` is a pure function over the profile
  and the catalogue: same inputs, same answer, no subprocess, no network.
* **Does anything leave without a click?** :func:`authorise_pull` is the only
  gate, and it refuses without an explicit ``accept``.

The catalogue lives here (policy), the interface reads it through the API. The
values in ``uhb/data/model_catalog.json`` carry ``verified``: an entry that no
local measurement or published digest backs stays ``false`` and is shown with its
warning instead of being presented as safe.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Free VRAM kept aside for the desktop, the KV cache and the driver.
MARGIN_GB = 0.8

#: A machine without a measurable GPU gets the smallest entries only.
CPU_ONLY_MAX_GB = 4.0

#: Every probe is bounded: a driver that hangs must not hang the harness.
PROBE_TIMEOUT = 10.0

CATALOG_PATH = Path(__file__).with_name("model_catalog.json")

GIB = 1024**3


class MachineError(RuntimeError):
    """Refusal carrying the HTTP status the caller should use."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# --- measurement ----------------------------------------------------------- #


def _run(command: list[str]) -> str | None:
    """Run a probe command, or return ``None``. Never raises, never hangs."""
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _gpu_from_nvidia() -> dict[str, Any] | None:
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return None
    line = out.strip().splitlines()[0]
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 3:
        return None
    try:
        total = round(int(float(parts[1])) / 1024, 1)
        free = round(int(float(parts[2])) / 1024, 1)
    except ValueError:
        return None
    return {
        "vendor": "nvidia",
        "name": parts[0],
        "vram_total_gb": total,
        "vram_free_gb": free,
        "source": "nvidia-smi",
    }


def _gpu_from_rocm() -> dict[str, Any] | None:
    """AMD, best effort.

    ``rocm-smi --showmeminfo vram`` prints one block per card with ``Total Used``
    lines; the shape differs between ROCm releases, so anything unexpected
    becomes ``None`` rather than a made-up number. This path is **not exercised
    on this workstation** (no AMD card) — it is written from the documented
    output and tested only through its fallback.
    """
    out = _run(["rocm-smi", "--showmeminfo", "vram"])
    if not out:
        return None
    numbers = [int(value) for value in re.findall(r"(\d{6,})", out)]
    if len(numbers) < 2:
        return None
    total = round(numbers[0] / GIB, 1)
    free = round((numbers[0] - numbers[1]) / GIB, 1)
    if total <= 0:
        return None
    return {
        "vendor": "amd",
        "name": "AMD GPU",
        "vram_total_gb": total,
        "vram_free_gb": free,
        "source": "rocm-smi (non vérifié sur ce poste)",
    }


def _gpu_from_system_profiler() -> dict[str, Any] | None:
    """Apple Silicon, best effort, same rule: parse or admit nothing."""
    out = _run(["system_profiler", "SPDisplaysDataType"])
    if not out:
        return None
    name = re.search(r"Chipset Model:\s*(.+)", out)
    vram = re.search(r"VRAM \(Total\):\s*(\d+)\s*MB", out)
    if not name:
        return None
    total = round(int(vram.group(1)) / 1024, 1) if vram else None
    return {
        "vendor": "apple",
        "name": name.group(1).strip(),
        "vram_total_gb": total,
        "vram_free_gb": None,
        "source": "system_profiler",
    }


def measure_gpu() -> dict[str, Any] | None:
    for probe in (_gpu_from_nvidia, _gpu_from_rocm, _gpu_from_system_profiler):
        found = probe()
        if found:
            return found
    return None


def _windows_memory() -> tuple[float | None, float | None]:
    import ctypes

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None, None
    return round(status.ullTotalPhys / GIB, 1), round(status.ullAvailPhys / GIB, 1)


def _ram() -> dict[str, Any]:
    try:
        if sys.platform.startswith("win"):
            total, free = _windows_memory()
        elif sys.platform == "darwin":
            out = _run(["sysctl", "-n", "hw.memsize"])
            total = (
                round(int(out.strip()) / GIB, 1)
                if out and out.strip().isdigit()
                else None
            )
            free = None  # macOS exposes no simple free figure without vm_stat maths
        else:
            total = free = None
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        total = round(int(line.split()[1]) * 1024 / GIB, 1)
                    elif line.startswith("MemAvailable:"):
                        free = round(int(line.split()[1]) * 1024 / GIB, 1)
    except Exception:  # noqa: BLE001 - a probe must never break the scan
        return {"total_gb": None, "free_gb": None, "source": None}
    return {
        "total_gb": total,
        "free_gb": free,
        "source": {
            "win32": "GlobalMemoryStatusEx",
            "darwin": "sysctl",
            "linux": "/proc/meminfo",
        }.get(sys.platform, "inconnu"),
    }


def measure_profile() -> dict[str, Any]:
    """Describe this machine. ``unknown`` names everything that could not be read."""
    gpu = measure_gpu()
    ram = _ram()
    try:
        disk = round(shutil.disk_usage(Path.home()).free / GIB, 1)
    except OSError:
        disk = None
    cpu_name = platform.processor() or platform.machine() or None
    profile = {
        "os": platform.system(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "cpu": {"model": cpu_name, "cores": os.cpu_count()},
        "ram": ram,
        "disk": {"home_free_gb": disk},
        "gpu": gpu,
    }
    unknown = []
    if not gpu:
        unknown.append("gpu")
    elif gpu.get("vram_free_gb") is None:
        unknown.append("vram_free_gb")
    if ram.get("free_gb") is None:
        unknown.append("ram_free_gb")
    if disk is None:
        unknown.append("disk_home_free_gb")
    profile["unknown"] = unknown
    return profile


# --- catalogue ------------------------------------------------------------- #


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    """Read and validate the catalogue. A malformed entry is an error, not a skip."""
    target = Path(path) if path else CATALOG_PATH
    data = json.loads(target.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise MachineError(f"catalogue vide ou mal formé : {target}", status=500)
    for entry in entries:
        for key in ("id", "engine", "file_gb", "needs", "license", "verified"):
            if key not in entry:
                raise MachineError(
                    f"entrée incomplète ({key}) : {entry.get('id')!r}", status=500
                )
        for key in ("vram_gb", "ram_gb", "disk_gb"):
            if key not in entry["needs"]:
                raise MachineError(
                    f"besoins incomplets ({key}) : {entry['id']!r}", status=500
                )
    return data


def _entry_by_id(catalog: dict[str, Any], entry_id: str) -> dict[str, Any]:
    for entry in catalog["entries"]:
        if entry["id"] == entry_id:
            return entry
    raise MachineError(f"modèle inconnu du catalogue : {entry_id}", status=404)


def _fits_vram(entry: dict[str, Any], vram_free: float | None) -> bool:
    return vram_free is not None and vram_free >= entry["needs"]["vram_gb"] + MARGIN_GB


def _fits_offload(
    entry: dict[str, Any], vram_free: float | None, ram_free: float | None
) -> bool:
    """A mixture-of-experts entry may exceed VRAM if the experts can sit in RAM."""
    if not entry.get("moe") or not entry.get("offloadable"):
        return False
    if vram_free is None or ram_free is None:
        return False
    return vram_free + ram_free >= entry["needs"]["vram_gb"] + MARGIN_GB


def _public(entry: dict[str, Any], mode: str, warning: str | None) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "engine": entry["engine"],
        "license": entry["license"],
        "file_gb": entry["file_gb"],
        "needs": dict(entry["needs"]),
        "params_total_b": entry.get("params_total_b"),
        "params_active_b": entry.get("params_active_b"),
        "verified": bool(entry["verified"]),
        "source_url": entry.get("source_url"),
        "notes": entry.get("notes"),
        "mode": mode,
        "warning": warning,
        # A warning means a human decided, not a default: never pre-ticked.
        "default_checked": warning is None,
    }


def recommend(
    profile: dict[str, Any], catalog: dict[str, Any], category: str = "chat"
) -> dict[str, Any]:
    """Pick what this machine can run, best first. Pure function."""
    gpu = profile.get("gpu") or {}
    vram = gpu.get("vram_free_gb")
    ram = (profile.get("ram") or {}).get("free_gb")
    entries = [
        e for e in catalog["entries"] if category in e.get("categories", ["chat"])
    ]
    if not entries:
        raise MachineError(f"aucun modèle pour la catégorie {category}", status=404)

    reasons: list[str] = []
    if vram is None:
        reasons.append("VRAM non mesurée : seuls les petits modèles sont proposés")
        small = sorted(
            (e for e in entries if e["needs"]["vram_gb"] <= CPU_ONLY_MAX_GB),
            key=lambda e: -e["file_gb"],
        )
        if not small:
            return {"entry": None, "alternatives": [], "reasons": reasons}
        chosen = small[0]
        alternatives = [
            {
                **_public(e, "offload", "lenteur non mesurée : experts en RAM"),
                "default_checked": False,
            }
            for e in sorted(entries, key=lambda e: -e["file_gb"])
            if e is not chosen and _fits_offload(e, vram, ram)
        ]
        return {
            "entry": _public(chosen, "cpu", None),
            "alternatives": alternatives,
            "reasons": reasons,
        }

    fits = sorted(
        (e for e in entries if _fits_vram(e, vram)),
        key=lambda e: -e["file_gb"],
    )
    offload = sorted(
        (e for e in entries if not _fits_vram(e, vram) and _fits_offload(e, vram, ram)),
        key=lambda e: -e["file_gb"],
    )
    alternatives = [
        {
            **_public(
                e,
                "offload",
                f"ne tient pas en {vram:g} Go de VRAM : experts en RAM, "
                "lenteur non mesurée",
            ),
            "default_checked": False,
        }
        for e in offload
    ]
    if fits:
        reasons.append(
            f"{vram:g} Go de VRAM libres : {fits[0]['id']} tient avec la marge de "
            f"{MARGIN_GB:g} Go"
        )
        return {
            "entry": _public(fits[0], "vram", None),
            "alternatives": alternatives,
            "reasons": reasons,
        }
    reasons.append(f"aucun modèle du catalogue ne tient dans {vram:g} Go de VRAM")
    return {"entry": None, "alternatives": alternatives, "reasons": reasons}


def scan(
    profile: dict[str, Any] | None = None,
    catalog: dict[str, Any] | None = None,
    category: str = "chat",
) -> dict[str, Any]:
    """Profile + catalogue + recommendation, with the reasons that led there."""
    measured = profile if profile is not None else measure_profile()
    book = catalog if catalog is not None else load_catalog()
    verdict = recommend(measured, book, category)
    return {
        "profile": measured,
        "catalog": {
            "version": book.get("version"),
            "updated": book.get("updated"),
            "count": len(book["entries"]),
        },
        **verdict,
    }


# --- consent gate ---------------------------------------------------------- #

OLLAMA_URL = os.environ.get("UHB_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")


def _ollama_pull(
    tag: str, on_progress: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Ask Ollama to pull a model, streaming its NDJSON progress."""
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/pull",
        data=json.dumps({"model": tag, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last: dict[str, Any] = {}
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if on_progress:
                    on_progress(last)
    except (urllib.error.URLError, OSError) as exc:
        raise MachineError(
            f"Ollama injoignable sur {OLLAMA_URL} : {exc}", status=502
        ) from exc
    return last


def authorise_pull(entry: dict[str, Any], profile: dict[str, Any], accept: bool) -> str:
    """Return the allowed mode, or raise. Nothing downloads without this."""
    if not accept:
        raise MachineError(
            "téléchargement refusé : l'accord explicite (accept: true) est obligatoire",
            status=403,
        )
    gpu = profile.get("gpu") or {}
    vram = gpu.get("vram_free_gb")
    ram = (profile.get("ram") or {}).get("free_gb")
    if _fits_vram(entry, vram):
        return "vram"
    if _fits_offload(entry, vram, ram):
        return "offload"
    raise MachineError(
        f"{entry['id']} ne tient pas ici : {entry['needs']['vram_gb']:g} Go de VRAM requis "
        f"(+ {MARGIN_GB:g} Go de marge), VRAM libre mesurée : "
        f"{'inconnue' if vram is None else f'{vram:g} Go'}",
        status=409,
    )


def status_directory(status_dir: Path | None = None) -> Path:
    """Where pull progress lives. Overridable so tests never touch the real home."""
    if status_dir is not None:
        return Path(status_dir)
    configured = os.environ.get("UHB_MODELS_STATUS_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".uhb" / "models"


def start_pull(
    entry_id: str,
    accept: bool,
    profile: dict[str, Any] | None = None,
    catalog: dict[str, Any] | None = None,
    pull_fn: Callable[[str, Callable[[dict[str, Any]], None] | None], dict[str, Any]]
    | None = None,
    token: str | None = None,
    status_dir: Path | None = None,
) -> dict[str, Any]:
    """Authorise, then run the pull in the background and report where to watch it."""
    book = catalog if catalog is not None else load_catalog()
    entry = _entry_by_id(book, entry_id)
    measured = profile if profile is not None else measure_profile()
    mode = authorise_pull(entry, measured, accept)

    run_id = (
        token or f"{int(time.time())}-{entry_id.replace(':', '-').replace('/', '-')}"
    )
    directory = status_directory(status_dir)
    directory.mkdir(parents=True, exist_ok=True)
    status_file = directory / f"pull-{run_id}.json"

    def write(payload: dict[str, Any]) -> None:
        # Écriture atomique : le suiveur lit ce fichier pendant que le fil écrit,
        # et un JSON à moitié écrit n'est pas un progrès, c'est une exception.
        temporary = status_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, status_file)

    write(
        {
            "id": run_id,
            "entry_id": entry_id,
            "mode": mode,
            "state": "starting",
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )

    def worker() -> None:
        try:
            last = (pull_fn or _ollama_pull)(
                entry_id,
                lambda progress: write(
                    {
                        "id": run_id,
                        "entry_id": entry_id,
                        "mode": mode,
                        "state": "downloading",
                        **progress,
                    }
                ),
            )
            write(
                {
                    "id": run_id,
                    "entry_id": entry_id,
                    "mode": mode,
                    "state": "done",
                    "result": last,
                }
            )
        except Exception as exc:  # noqa: BLE001 - the worker must record its failure
            write(
                {
                    "id": run_id,
                    "entry_id": entry_id,
                    "mode": mode,
                    "state": "failed",
                    "error": str(exc),
                }
            )

    threading.Thread(target=worker, name=f"uhb-pull-{run_id}", daemon=True).start()
    return {
        "started": True,
        "id": run_id,
        "mode": mode,
        "status_url": f"/models/pull/{run_id}",
        "entry": _public(entry, mode, None),
    }


def pull_status(run_id: str, status_dir: Path | None = None) -> dict[str, Any]:
    directory = status_directory(status_dir)
    target = directory / f"pull-{run_id}.json"
    if not target.is_file():
        raise MachineError(f"téléchargement inconnu : {run_id}", status=404)
    return json.loads(target.read_text(encoding="utf-8"))


def uninstall_command(entry_id: str) -> str:
    """Documented removal: what the interface shows next to a downloaded model."""
    return f"ollama rm {entry_id}"
