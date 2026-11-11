"""``uhb`` — the command line.

Seven commands, in two families. Read-only: ``status``, ``config``, ``modes``,
``providers``, ``health``. Acting: ``execute``, ``run``, ``update``.

Nothing here prints an environment value. A provider's key is reported as
``loaded`` or ``MISSING``, next to the *variable name* — never the value, never
a fragment of it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .. import __version__
from ..adapters.hermes import HermesAdapter
from ..core.executor import Executor
from ..core.health import Health
from ..core.router import Router
from ..core.types import TaskContext
from ..hub.modes import ModesError, ModeSpec, get_mode, load_modes
from ..hub.router import select_model
from . import panel, settings, status as status_module, update as update_module
from ..adapters.second_brain import SecondBrainError

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8101


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uhb",
        description="Universal Harness Backend: modes, routing and a compiled wiki for any harness.",
    )
    parser.add_argument(
        "--version", action="version", version=f"UHB (Universal Harness Backend) {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="version, providers, routing policy, modes")
    status.add_argument("--workspace", default="")
    status.add_argument("--json", action="store_true")

    config = sub.add_parser("config", help="read or write the UHB configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_list = config_sub.add_parser("list", help="every effective key and its value")
    config_list.add_argument("--json", action="store_true")
    config_get = config_sub.add_parser("get", help="one key, dotted path")
    config_get.add_argument("key")
    config_set = config_sub.add_parser("set", help="write one key (credentials refused)")
    config_set.add_argument("key")
    config_set.add_argument("value")
    config_set.add_argument("--file", default="", help="config path (default: uhb/config.yaml)")

    modes = sub.add_parser("modes", help="list execution modes, or show one")
    modes_sub = modes.add_subparsers(dest="modes_command", required=True)
    modes_sub.add_parser("list", help="name, tier, write scope and turn budget")
    modes_show = modes_sub.add_parser("show", help="the full profile of one mode")
    modes_show.add_argument("name")

    execute = sub.add_parser("execute", help="run one turn through a harness")
    execute.add_argument("prompt")
    execute.add_argument("--workspace", default="")
    execute.add_argument("--model", default="")
    execute.add_argument("--mode", default="", help="execution mode; the router picks the model")
    execute.add_argument("--json", action="store_true")

    run = sub.add_parser("run", help="start the HTTP entrypoint")
    run.add_argument("--port", type=int, default=DEFAULT_PORT)
    run.add_argument("--host", default=DEFAULT_HOST)
    run.add_argument("--mode", default="chat", help="mode the adapter acts for (default: chat, read-only)")
    run.add_argument("--workspace", default="")

    docs = sub.add_parser("docs", help="path and URL of the embedded manual")
    docs.add_argument("--port", type=int, default=DEFAULT_PORT)
    docs.add_argument("--host", default=DEFAULT_HOST)
    docs.add_argument("--json", action="store_true")

    brain = sub.add_parser("brain", help="read the local second brain (RAG, SiYuan, MARI)")
    brain_sub = brain.add_subparsers(dest="brain_command", required=True)
    brain_status = brain_sub.add_parser("status", help="probe the three services")
    brain_status.add_argument("--json", action="store_true")
    brain_status.add_argument("--strict", action="store_true", help="exit 1 if any service is down")
    brain_search = brain_sub.add_parser("search", help="ask the RAG")
    brain_search.add_argument("question")
    brain_search.add_argument("-k", "--k", type=int, default=5, help="passages to retrieve (1-20)")
    brain_search.add_argument("--json", action="store_true")
    brain_start = brain_sub.add_parser("start", help="launch an installed local service")
    brain_start.add_argument("--only", choices=("rag", "siyuan", "mari"), default=None,
                             help="which service (default: every one installed)")
    brain_start.add_argument("--dry-run", action="store_true")
    brain_start.add_argument("--port", type=int, default=None)
    for alias in ("rag", "siyuan", "mari"):
        direct = brain_sub.add_parser(f"start-{alias}", help=f"launch {alias} (same as start --only {alias})")
        direct.add_argument("--dry-run", action="store_true")
        direct.add_argument("--port", type=int, default=None)
    brain_read = brain_sub.add_parser("read", help="export a SiYuan document as markdown")
    brain_read.add_argument("page_id")
    brain_read.add_argument("--json", action="store_true")

    ui = sub.add_parser("ui", help="what to paste into a dashboard, and where the guide is")
    ui.add_argument("--base", default=f"http://{DEFAULT_HOST}:{DEFAULT_PORT}",
                    help="where UHB listens, as the browser will call it")
    ui.add_argument("--markup", action="store_true", help="print the widget markup and exit")
    ui.add_argument("--json", action="store_true")

    health = sub.add_parser("health", help="report backend health")
    health.add_argument("--workspace", default="")

    sub.add_parser("providers", help="list configured providers")

    update = sub.add_parser("update", help="fast-forward this checkout from its remote")
    update.add_argument("--dry-run", action="store_true", help="fetch and report, do not pull")
    update.add_argument("--remote", default="origin")
    update.add_argument("--json", action="store_true")

    return parser


# --- read-only commands ----------------------------------------------------


def _status(args: argparse.Namespace) -> int:
    data = status_module.snapshot(args.workspace or None)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(status_module.render(data))
    return EXIT_OK


def _config(args: argparse.Namespace) -> int:
    if args.config_command == "list":
        flat = settings.flatten(settings.load())
        if args.json:
            print(json.dumps(flat, indent=2, ensure_ascii=False, default=str))
        else:
            for key in sorted(flat):
                value = flat[key]
                shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                print(f"{key} = {shown}")
        return EXIT_OK

    if args.config_command == "get":
        value = settings.get(settings.load(), args.key)
        print(value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False))
        return EXIT_OK

    _data, written = settings.set_value(args.key, args.value, args.file or None)
    shown = written if isinstance(written, str) else json.dumps(written, ensure_ascii=False)
    print(f"{args.key} = {shown}")
    print(f"# written to {settings.config_path(args.file or None)}")
    return EXIT_OK


def _modes(args: argparse.Namespace) -> int:
    if args.modes_command == "list":
        for name, spec in sorted(load_modes().items()):
            scope = "read-only" if not spec.write else f"{len(spec.write)} write glob(s)"
            print(f"{name:<8} {spec.kind:<11} tier={spec.tier:<5} {scope:<20} {spec.max_turns} turn(s)")
        return EXIT_OK

    print(json.dumps(get_mode(args.name).to_dict(), indent=2, ensure_ascii=False))
    return EXIT_OK


def _providers(_args: argparse.Namespace) -> int:
    rows = status_module.providers_facts(Router.from_config())
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return EXIT_OK


# --- acting commands -------------------------------------------------------


async def _execute(args: argparse.Namespace) -> int:
    mode_name = (getattr(args, "mode", "") or "").strip()
    spec: ModeSpec | None = None
    model_ref = args.model
    if mode_name:
        spec = get_mode(mode_name)  # unknown mode fails here, before any turn
        model_ref = select_model(args.prompt, spec, model_ref)
    adapter = HermesAdapter(mode=mode_name or "chat")
    executor = Executor(adapter)
    context = TaskContext(
        workspace=args.workspace, model_ref=model_ref, metadata={"mode": mode_name}
    )
    async for event in executor.execute_task(args.prompt, context):
        if args.json:
            print(json.dumps(event.to_dict(), ensure_ascii=False))
        elif event.type == "token" and event.content:
            print(event.content)
        elif event.type == "error":
            print(f"[error] {event.content}", file=sys.stderr)
            return EXIT_FAIL
    return EXIT_OK


async def _health(args: argparse.Namespace) -> int:
    result = await Health(workspace=args.workspace or None).health()
    print(json.dumps({"status": result.status, "checks": result.checks,
                      "timestamp": result.timestamp}, indent=2, ensure_ascii=False))
    return EXIT_OK if result.status == "ok" else EXIT_FAIL


def _run(args: argparse.Namespace) -> int:
    get_mode(args.mode)  # an unknown mode fails before the socket opens
    from . import server as server_module

    server_module.Handler.adapter = HermesAdapter(mode=args.mode)
    server_module.Handler.workspace = args.workspace
    server_module.Handler.mode = args.mode
    providers = status_module.providers_facts(Router.from_config())
    ready = sum(1 for row in providers if row["key_loaded"] or row["local"])
    print(panel.banner(
        version=__version__, mode=args.mode, host=args.host, port=args.port,
        providers_ready=ready, providers_total=len(providers),
    ))
    print()
    server_module.serve(port=args.port, host=args.host)
    return EXIT_OK


def _brain(args: argparse.Namespace) -> int:
    """The local second brain: three probes, or one RAG query."""
    from ..adapters.second_brain import SecondBrainAdapter

    brain = SecondBrainAdapter()
    if args.brain_command == "status":
        states = brain.status()
        if args.json:
            print(json.dumps([state.to_dict() for state in states], indent=2, ensure_ascii=False))
        else:
            for state in states:
                mark = "●" if state.online else "○"
                print(f"{mark} {state.name:<7} {state.detail[:46]:<46} {state.latency_ms:6.0f} ms  {state.url}")
            down = [state.name for state in states if not state.online]
            if down:
                print(f"down: {', '.join(down)}")
            else:
                print("all services answered")
        return EXIT_FAIL if (args.strict and any(not s.online for s in states)) else EXIT_OK

    if args.brain_command == "start":
        return _brain_start(args)
    if args.brain_command.startswith("start-"):
        args.only = args.brain_command.split("-", 1)[1]
        return _brain_start(args)
    if args.brain_command == "read":
        page = brain.read_siyuan(args.page_id)
        if args.json:
            print(json.dumps(page, indent=2, ensure_ascii=False))
            return EXIT_OK
        print(f"SiYuan · {page['hPath'] or page['id']} · {len(page['markdown'])} caractères")
        print()
        print(page["markdown"])
        return EXIT_OK

    result = brain.search(args.question, k=args.k)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK
    if not result.fragments:
        print(f"aucun passage pour « {result.question} » (RAG en ligne, 0 résultat)")
        return EXIT_OK
    print(f"RAG · {len(result.fragments)} passage(s) pour « {result.question} »")
    for position, fragment in enumerate(result.fragments, 1):
        excerpt = " ".join(fragment.excerpt.split())
        if len(excerpt) > 240:
            excerpt = excerpt[:239] + "…"
        print()
        print(f"{position}. [{fragment.score:.4f}] {fragment.source} · {fragment.title} §{fragment.part}")
        if fragment.path:
            print(f"   {fragment.path}")
        print(f"   {excerpt}")
    return EXIT_OK


def _ui(args: argparse.Namespace) -> int:
    """The widget, ready to paste, plus the state of the server that feeds it."""
    markup = panel.snippet(version=__version__, base=args.base.rstrip("/"))
    if args.markup:
        print(markup)
        return EXIT_OK
    guide = panel.DOCS_DIR / "UI_INTEGRATION.md"
    server_up = _server_up(args.base.split("//")[-1].split(":")[0], int(args.base.rsplit(":", 1)[-1]))
    payload = {
        "version": __version__,
        "base": args.base.rstrip("/"),
        "status_url": args.base.rstrip("/") + "/status",
        "server": server_up,
        "guide": str(guide),
        "guide_installed": guide.is_file(),
        "markup_chars": len(markup),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    print(f"UHB {__version__} — le widget est prêt pour l'interface.")
    print()
    print(f"  serveur   {payload['status_url']}  ({'actif' if server_up else 'arrêté — lancez uhb run'})")
    print(f"  guide     {guide if guide.is_file() else str(guide) + ' (absent)'}")
    print(f"  markup    uhb ui --markup  →  {payload['markup_chars']} caractères de HTML/CSS/JS")
    print()
    print("Collez le markup avant </body>, puis ajustez data-uhb-base si UHB n'est pas")
    print("sur http://127.0.0.1:8101. Le point interroge /status toutes les ouvertures.")
    return EXIT_OK


def _brain_start(args: argparse.Namespace) -> int:
    """Launch what is actually installed, or say precisely what is missing."""
    import subprocess
    import sys
    from pathlib import Path

    from ..services import install as services_install

    bin_dir = services_install.BIN_DIR
    data_dir = bin_dir / "data"
    wanted = [args.only] if args.only else ["siyuan", "rag", "mari"]
    launched: list[tuple[str, str, list[str]]] = []
    missing: list[str] = []

    for name in wanted:
        if name == "siyuan":
            found = sorted(bin_dir.joinpath("siyuan").glob("siyuan-*")) + sorted(
                bin_dir.joinpath("siyuan").glob("SiYuan-Kernel*"))
            if not found:
                missing.append(f"siyuan: run `install_services.sh --with siyuan` (or .ps1)")
                continue
            exe = found[0]
            workspace = data_dir / "siyuan"
            argv = (
                [str(exe), f"--workspace={workspace}"]
                if sys.platform.startswith("win")
                else [str(exe), "serve", f"--workspace={workspace}", "--port=6806"]
            )
            launched.append((name, "http://127.0.0.1:6806", argv))

        elif name == "rag":
            service = Path(__file__).resolve().parents[1] / "services" / "rag" / "serveur_rag.py"
            try:
                import faiss  # noqa: F401
                import fastapi  # noqa: F401
            except ImportError:
                missing.append(
                    "rag: dependencies absent — `python -m uhb.services.install --with rag --yes`")
                continue
            argv = [sys.executable, str(service), "--port", str(args.port or 8200)]
            launched.append((name, f"http://127.0.0.1:{args.port or 8200}", argv))

        elif name == "mari":
            candidates = [bin_dir / "mari" / "Bridge.exe", bin_dir / "mari" / "Bridge.dll"]
            found = next((path for path in candidates if path.is_file()), None)
            if found is None:
                missing.append(
                    "mari: run `python -m uhb.services.install --with mari` "
                    "(bridge ships here; needs `git submodule update`)")
                continue
            launched.append((name, "http://127.0.0.1:55553", [str(found)]))

    for line in missing:
        print(f"  skip  {line}")
    if not launched:
        print("nothing to start: no service is installed (see above).")
        return EXIT_FAIL

    for name, url, argv in launched:
        printable = " ".join(argv)
        if args.dry_run:
            print(f"  {name:7} {url:26} {printable}")
            continue
        data_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            argv, cwd=str(data_dir),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=not sys.platform.startswith("win"),
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0,
        )
        print(f"  {name:7} {url:26} pid {process.pid}")

    if args.dry_run:
        print()
        print("dry-run: nothing launched.")
    else:
        print()
        print("Services left running. Stop them with the pid above:")
        print("  Windows: taskkill /PID <pid> /F      Linux/macOS: kill <pid>")
    return EXIT_OK


def _docs(args: argparse.Namespace) -> int:
    """Where the manual is, and whether a server would serve it right now."""
    path = panel.manual_path("fr")
    english = panel.manual_path("en")
    base = f"http://{args.host}:{args.port}"
    payload = {
        "manual": str(path),
        "installed": path.is_file(),
        "size": path.stat().st_size if path.is_file() else 0,
        "english": {
            "path": str(english),
            "installed": english.is_file(),
            "size": english.stat().st_size if english.is_file() else 0,
        },
        "url": f"{base}/docs/manuel.pdf",
        "url_en": f"{base}/docs/manual.pdf",
        "server": _server_up(args.host, args.port),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    if path.is_file():
        print(f"manual   {path} ({payload['size']} octets)")
    else:
        print(f"manual   absent ({path})")
        print("         installez le paquet avec ses données : pip install -e . depuis un checkout")
    if english.is_file():
        print(f"english  {english} ({payload['english']['size']} octets)")
    print(f"url      {payload['url']}  ({'serveur actif' if payload['server'] else 'serveur arrêté'})")
    print(f"url en   {payload['url_en']}")
    return EXIT_OK


def _server_up(host: str, port: int) -> bool:
    """A 400 ms probe: is something answering on /status?"""
    import httpx

    try:
        response = httpx.get(f"http://{host}:{port}/status", timeout=0.4)
    except Exception:
        return False
    return response.status_code == 200



def _update(args: argparse.Namespace) -> int:
    result = update_module.run_update(dry_run=args.dry_run, remote=args.remote)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK
    print(f"repository  {result.branch} @ {result.before}")
    if result.dry_run:
        print(f"pending     {result.pending} commit(s) behind the remote")
        print("dry run     nothing pulled")
    else:
        print(f"result      {result.detail}")
        for check in result.checks:
            print(f"  - {check}")
    return EXIT_OK


# --- dispatch --------------------------------------------------------------


def _ensure_utf8_streams() -> None:
    """Windows consoles default to cp1252/cp850: an arrow or an accent in a
    message would raise UnicodeEncodeError. Keep the console alive instead."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_streams()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            return _status(args)
        if args.command == "config":
            return _config(args)
        if args.command == "modes":
            return _modes(args)
        if args.command == "providers":
            return _providers(args)
        if args.command == "execute":
            return asyncio.run(_execute(args))
        if args.command == "ui":
            return _ui(args)
        if args.command == "brain":
            return _brain(args)
        if args.command == "docs":
            return _docs(args)
        if args.command == "health":
            return asyncio.run(_health(args))
        if args.command == "run":
            return _run(args)
        if args.command == "update":
            return _update(args)
    except (settings.SettingsError, update_module.UpdateError, ModesError, SecondBrainError) as exc:
        print(f"[uhb] {exc}", file=sys.stderr)
        return EXIT_USAGE
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
