"""Intent routing — decide which model answers, and what to do when it cannot.

The router is a pure function plus one injection point:

1. **classify** the request (word count, code markers, multi-step verbs, paths,
   multiple questions) into ``trivial`` / ``standard`` / ``complex``. Deterministic,
   no model call, so routing a question costs microseconds rather than a token bill.
2. **plan** the candidate chain: start at the mode's tier, walk ``eco`` -> ``free``
   -> ``paid``, keep only the models that declare support for that complexity, and
   drop the ``paid`` tier unless the mode says ``allow_paid: true``.
3. **execute** the chain: first candidate that answers wins; a failure moves to the
   next one and is recorded. If nothing answers, :class:`AllProvidersFailed` is
   raised with the attempts — never an empty string pretending to be an answer.

Nothing here knows about a harness, and nothing knows about a vendor: provider
coordinates are read from the UHB provider table (``uhb/core/router.py``,
``uhb/config.yaml``) while tier and complexity live in ``catalog.json``. Adding a
provider means editing the provider table; adding a model to the policy means one
line in ``catalog.json``.

    from uhb.hub.modes import get_mode
    from uhb.hub.router import complete

    result = complete("summarise this diff", mode=get_mode("review"))
    result.provider, result.tier, result.attempts
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ...core.router import Router, UnknownModelRef
from ..modes import ModeSpec, get_mode

#: Complexity classes, cheapest first.
COMPLEXITIES = ("trivial", "standard", "complex")

#: Cost tiers, in the order the chain walks them.
TIERS = ("eco", "free", "paid")

#: Shipped policy. Override with ``UHB_ROUTER_CATALOG`` for a different fleet.
CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"
CATALOG_ENV = "UHB_ROUTER_CATALOG"

#: A chain longer than this is a configuration accident, not a fallback plan.
MAX_CANDIDATES = 8

#: Classification thresholds, in signal points. Documented, not tuned by feel.
LONG_REQUEST_WORDS = 120
STANDARD_REQUEST_WORDS = 40
COMPLEX_SCORE = 4
STANDARD_SCORE = 2

MULTI_STEP_VERBS = (
    "refactor",
    "migrate",
    "implement",
    "design",
    "debug",
    "optimise",
    "optimize",
    "rewrite",
    "bench",
    "trace",
    "compare",
    "plan",
    "document",
    "fix",
    "add",
    "remove",
    "delete",
    "rename",
    "merge",
    "split",
    "audit",
    "review",
)

#: Whole words only, inflections included: "add" must not fire inside "address".
_VERB_RE = re.compile(r"\b(?:" + "|".join(MULTI_STEP_VERBS) + r")(?:s|es|ed|ing)?\b")

CODE_MARKERS = ("```", "def ", "class ", "Traceback", "SELECT ")

PATH_MARKER = re.compile(r"[\w./-]+\.(?:py|ts|js|md|json|ya?ml|sh|ps1|toml)\b")

#: A sender performs one call: candidate in, raw text out. Injectable for tests.
Sender = Callable[["Candidate", str, int], str]


class RouterError(RuntimeError):
    """Base class for routing failures."""


class InvalidCatalog(RouterError, ValueError):
    """The routing policy is malformed, or names a model no provider can serve."""


class UnroutableRequest(RouterError):
    """No model in the allowed tiers supports this complexity."""


class AllProvidersFailed(RouterError):
    """Every candidate failed. Carries the attempts, never a fake answer."""

    def __init__(self, message: str, attempts: tuple["Attempt", ...]) -> None:
        super().__init__(message)
        self.attempts = attempts


# --- classification --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Classification:
    complexity: str
    score: int
    signals: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {"complexity": self.complexity, "score": self.score, "signals": dict(self.signals)}


def classify(request: str) -> Classification:
    """Score a request from surface signals only. Same input, same class, always."""
    text = request or ""
    lowered = text.lower()
    words = len(text.split())
    signals = {
        "length": 2 if words > LONG_REQUEST_WORDS else (1 if words > STANDARD_REQUEST_WORDS else 0),
        "code": 1 if any(marker in text for marker in CODE_MARKERS) else 0,
        "multi_step": min(3, len({match.lower() for match in _VERB_RE.findall(lowered)})),
        "paths": 1 if len(PATH_MARKER.findall(text)) >= 2 else 0,
        "multi_question": 1 if text.count("?") >= 2 else 0,
    }
    score = sum(signals.values())
    if score >= COMPLEX_SCORE:
        complexity = "complex"
    elif score >= STANDARD_SCORE:
        complexity = "standard"
    else:
        complexity = "trivial"
    return Classification(complexity=complexity, score=score, signals=signals)


# --- catalog ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """One addressable model: the policy (tier) and the coordinates to reach it."""

    ref: str
    tier: str
    complexity: tuple[str, ...]
    protocol: str
    base_url: str
    api_key_env: str
    model_id: str

    def __str__(self) -> str:
        return f"{self.tier}/{self.ref}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "tier": self.tier,
            "complexity": list(self.complexity),
            "protocol": self.protocol,
            "base_url": self.base_url,
            "model_id": self.model_id,
            "api_key_env": self.api_key_env,
        }


@dataclass(frozen=True, slots=True)
class Catalog:
    tiers: tuple[str, ...]
    candidates: tuple[Candidate, ...]

    def for_tier(self, tier: str) -> tuple[Candidate, ...]:
        return tuple(candidate for candidate in self.candidates if candidate.tier == tier)


_CATALOG_CACHE: dict[str, Catalog] = {}


def _read_catalog(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvalidCatalog(f"{path}: cannot read the catalog ({exc})") from exc
    except json.JSONDecodeError as exc:
        raise InvalidCatalog(f"{path}: invalid JSON ({exc})") from exc


def load_catalog(
    path: str | Path | None = None,
    *,
    router: Router | None = None,
    refresh: bool = False,
) -> Catalog:
    """Load and validate the routing policy against the provider table.

    A reference the provider table cannot resolve is a hard error: a route that
    points nowhere is worse than no route, because it looks like a plan.
    """
    target = Path(path) if path else Path(os.environ.get(CATALOG_ENV) or CATALOG_PATH)
    key = str(target.resolve())
    if not refresh and key in _CATALOG_CACHE:
        return _CATALOG_CACHE[key]

    data = _read_catalog(target)
    tiers = tuple(data.get("tiers") or ())
    if not tiers:
        raise InvalidCatalog(f"{target}: 'tiers' is required")
    unknown_tiers = [tier for tier in tiers if tier not in TIERS]
    if unknown_tiers:
        raise InvalidCatalog(f"{target}: unknown tier(s): {', '.join(unknown_tiers)}")

    table = router or Router()
    candidates: list[Candidate] = []
    for position, entry in enumerate(data.get("models") or []):
        where = f"{target}: models[{position}]"
        if not isinstance(entry, dict):
            raise InvalidCatalog(f"{where} must be an object")
        ref = str(entry.get("ref") or "").strip()
        tier = str(entry.get("tier") or "").strip()
        declared = entry.get("complexity") or []
        if not ref or not tier:
            raise InvalidCatalog(f"{where}: 'ref' and 'tier' are required")
        if tier not in tiers:
            raise InvalidCatalog(f"{where}: tier {tier!r} is not declared in 'tiers'")
        if not isinstance(declared, list) or not declared:
            raise InvalidCatalog(f"{where}: 'complexity' must be a non-empty list")
        bad = [value for value in declared if value not in COMPLEXITIES]
        if bad:
            raise InvalidCatalog(f"{where}: unknown complexity {', '.join(map(str, bad))}")
        try:
            descriptor = table.resolve_provider(ref)
        except UnknownModelRef as exc:
            raise InvalidCatalog(f"{where}: {exc}") from exc
        candidates.append(
            Candidate(
                ref=ref,
                tier=tier,
                complexity=tuple(declared),
                protocol=descriptor.protocol,
                base_url=descriptor.base_url,
                api_key_env=descriptor.api_key_env,
                model_id=descriptor.model_id,
            )
        )

    if not candidates:
        raise InvalidCatalog(f"{target}: no model declared")
    catalog = Catalog(tiers=tiers, candidates=tuple(candidates))
    _CATALOG_CACHE[key] = catalog
    return catalog


def clear_cache() -> None:
    _CATALOG_CACHE.clear()


# --- planning --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """The plan, fully inspectable before a single token is spent."""

    mode: str
    complexity: str
    score: int
    signals: dict[str, int]
    candidates: tuple[Candidate, ...]
    allow_paid: bool
    reason: str

    @property
    def chosen(self) -> Candidate:
        return self.candidates[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "complexity": self.complexity,
            "score": self.score,
            "signals": dict(self.signals),
            "allow_paid": self.allow_paid,
            "reason": self.reason,
            "chain": [candidate.to_dict() for candidate in self.candidates],
        }


def _chain(tiers: Iterable[str], complexity: str, catalog: Catalog) -> list[Candidate]:
    allowed = list(tiers)
    plan: list[Candidate] = []
    for tier in allowed:
        for candidate in catalog.for_tier(tier):
            if complexity in candidate.complexity and candidate not in plan:
                plan.append(candidate)
    return plan[:MAX_CANDIDATES]


def route(
    request: str,
    mode: ModeSpec | str,
    *,
    complexity: str | None = None,
    catalog: Catalog | None = None,
    router: Router | None = None,
) -> RouteDecision:
    """Classify, then plan the chain. No network, no side effect."""
    spec = get_mode(mode) if isinstance(mode, str) else mode
    policy = catalog or load_catalog(router=router)

    if complexity is None:
        classification = classify(request)
        chosen_complexity, score, signals = (
            classification.complexity,
            classification.score,
            classification.signals,
        )
    else:
        if complexity not in COMPLEXITIES:
            raise UnroutableRequest(f"unknown complexity {complexity!r}; expected one of {', '.join(COMPLEXITIES)}")
        chosen_complexity, score, signals = complexity, -1, {"forced": 1}

    start = policy.tiers.index(spec.tier) if spec.tier in policy.tiers else 0
    tiers = list(policy.tiers[start:])
    if not spec.allow_paid:
        tiers = [tier for tier in tiers if tier != "paid"]

    candidates = _chain(tiers, chosen_complexity, policy)
    if not candidates:
        allowed = ", ".join(tiers) or "(none)"
        raise UnroutableRequest(
            f"mode {spec.name!r}: no model supports {chosen_complexity!r} in tiers {allowed}; "
            f"set allow_paid on the mode or declare a capable model in the catalog"
        )

    return RouteDecision(
        mode=spec.name,
        complexity=chosen_complexity,
        score=score,
        signals=signals,
        candidates=tuple(candidates),
        allow_paid=spec.allow_paid,
        reason=(
            f"{spec.name}/{chosen_complexity} -> {candidates[0]} "
            f"({len(candidates)} candidate(s): "
            + " > ".join(str(candidate.ref) for candidate in candidates)
            + ")"
        ),
    )


# --- execution -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Attempt:
    """One call in the chain. ``detail`` never carries a credential or a URL token."""

    provider: str
    outcome: str  # "ok" | "skipped" | "failed"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "outcome": self.outcome, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    provider: str
    model_id: str
    tier: str
    attempts: tuple[Attempt, ...]
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "tier": self.tier,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


def _local(base_url: str) -> bool:
    return "127.0.0.1" in base_url or "localhost" in base_url


def has_credentials(candidate: Candidate) -> bool:
    """A remote provider with no key named in the environment is unusable.

    Checked before the call so a missing key costs a skip, not a 401.
    """
    return _local(candidate.base_url) or candidate.api_key_env in os.environ


def _reasoning_effort() -> str:
    """Reasoning budget for a routed call. Compiling and routing are extraction.

    Same lever as the wiki's default LLM: measured on qwen3:8b, a ``none`` trace
    answers in ~1 s where the default trace spends ~7 s on a one-word reply.
    Set ``UHB_LLM_REASONING=default`` to let a thinking model think.
    """
    value = os.environ.get("UHB_LLM_REASONING", "none").strip()
    return "" if value.lower() in ("default", "omit") else value


def build_request(candidate: Candidate, prompt: str, max_tokens: int) -> tuple[str, dict[str, str], dict[str, Any]]:
    """``(url, headers, payload)`` for one candidate. Headers carry no value by default."""
    headers = {"content-type": "application/json"}
    reasoning = _reasoning_effort()
    if candidate.protocol == "anthropic":
        url = candidate.base_url.rstrip("/") + "/v1/messages"
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model": candidate.model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:  # openai, deepseek and anything OpenAI-compatible, Ollama included
        url = candidate.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": candidate.model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    if reasoning:
        payload["reasoning_effort"] = reasoning
    return url, headers, payload


def default_sender(candidate: Candidate, prompt: str, max_tokens: int) -> str:
    """Call one provider over HTTP. The key is read by name, used, and never stored."""
    import httpx  # local import: routing works with no HTTP stack installed

    url, headers, payload = build_request(candidate, prompt, max_tokens)
    token = os.environ.get(candidate.api_key_env)
    if token:
        if candidate.protocol == "anthropic":
            headers["x-api-key"] = token
        else:
            headers["authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=float(os.environ.get("UHB_LLM_TIMEOUT", "120"))) as client:
        response = client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
    if candidate.protocol == "anthropic":
        blocks = data.get("content") or []
        return "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise RouterError("unexpected response shape from the provider") from exc


def execute(
    decision: RouteDecision,
    prompt: str,
    *,
    sender: Sender | None = None,
    max_tokens: int = 2048,
) -> Completion:
    """Walk the chain. First answer wins; a failure is a step, not an exception."""
    call = sender or default_sender
    attempts: list[Attempt] = []
    started = time.perf_counter()
    for candidate in decision.candidates:
        if sender is None and not has_credentials(candidate):
            attempts.append(
                Attempt(str(candidate.ref), "skipped", f"no {candidate.api_key_env} in the environment")
            )
            continue
        try:
            text = call(candidate, prompt, max_tokens)
        except Exception as exc:  # a failing provider must not abort the chain
            attempts.append(Attempt(str(candidate.ref), "failed", type(exc).__name__))
            continue
        attempts.append(Attempt(str(candidate.ref), "ok"))
        return Completion(
            text=text,
            provider=candidate.ref,
            model_id=candidate.model_id,
            tier=candidate.tier,
            attempts=tuple(attempts),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    raise AllProvidersFailed(
        f"all {len(attempts)} candidate(s) failed for mode {decision.mode!r}: "
        + ", ".join(f"{attempt.provider} ({attempt.outcome})" for attempt in attempts),
        tuple(attempts),
    )


def complete(
    prompt: str,
    *,
    mode: ModeSpec | str,
    request: str | None = None,
    complexity: str | None = None,
    sender: Sender | None = None,
    catalog: Catalog | None = None,
    router: Router | None = None,
) -> Completion:
    """Route then execute. ``request`` defaults to the prompt for classification."""
    spec = get_mode(mode) if isinstance(mode, str) else mode
    decision = route(request if request is not None else prompt, spec, complexity=complexity, catalog=catalog, router=router)
    return execute(decision, prompt, sender=sender, max_tokens=spec.max_tokens)


def routed_llm(
    mode: ModeSpec | str,
    *,
    complexity: str | None = None,
    sender: Sender | None = None,
    catalog: Catalog | None = None,
    router: Router | None = None,
) -> Callable[[str], str]:
    """A prompt-in/text-out callable that goes through the router.

    This is the seam the pipeline uses: ``ingest`` receives an LLM like any
    other, and with a mode it receives *this* one — so the model is chosen by
    policy and complexity instead of by a hard-coded endpoint.

    ``complexity`` lets a pipeline that already knows its task class say so.
    A compile is a bounded extraction whatever the length of the schema it is
    given, and the router's length signal would otherwise read the schema as
    difficulty.
    """
    spec = get_mode(mode) if isinstance(mode, str) else mode

    def call(prompt: str) -> str:
        return complete(
            prompt,
            mode=spec,
            complexity=complexity,
            sender=sender,
            catalog=catalog,
            router=router,
        ).text

    call.__name__ = f"routed_llm[{spec.name}]"  # visible in tracebacks and logs
    return call


def select_model(prompt: str, mode: ModeSpec | str, model_ref: str = "") -> str:
    """An explicit model wins; otherwise the router decides from mode + prompt.

    This is what an entrypoint calls before handing a turn to a harness: the
    harness receives one model reference, already justified by policy.
    """
    if model_ref:
        return model_ref
    return route(prompt, mode).chosen.ref
