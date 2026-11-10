"""Generic Karpathy-style LLM wiki.

Three layers: ``raw/`` (immutable sources), ``wiki/`` (compiled pages) and
``wiki/schema.md`` (the conventions). Three operations: ``ingest`` compiles a
source into pages, ``query`` returns candidate pages instead of an answer, and
``lint`` reports structural rot.

    from uhb.hub.wiki import ingest, query, lint

    ingest("raw/notes/note.md", workspace="path/to/hub/wiki")
    query("how does the fallback chain work?")
    lint()
"""

from .scripts.ingest import IngestResult, ingest
from .scripts.lint import LintResult, lint
from .scripts.query import Page, QueryResult, query

__all__ = [
    "IngestResult",
    "LintResult",
    "Page",
    "QueryResult",
    "ingest",
    "lint",
    "query",
]
