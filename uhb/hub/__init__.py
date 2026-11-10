"""HUB — the reference brain behind UHB.

The HUB is what turns the eight pivot functions into something that learns:
a compiled wiki, execution modes, and a cost-aware intent router. This package
holds the wiki (Phase 1.1), the modes (Phase 1.2) and the router (Phase 1.3).
"""

from .modes import ModeSpec, get_mode, load_modes
from .router import Completion, RouteDecision, complete, route
from .wiki import IngestResult, LintResult, Page, QueryResult, ingest, lint, query

__all__ = [
    "Completion",
    "IngestResult",
    "LintResult",
    "ModeSpec",
    "Page",
    "QueryResult",
    "RouteDecision",
    "complete",
    "get_mode",
    "ingest",
    "lint",
    "load_modes",
    "query",
    "route",
]
