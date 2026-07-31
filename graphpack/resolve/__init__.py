"""Mention to canonical identifier.

Extraction produces entities named the way the text named them — `urllib3 2.0`,
`issue #369`, `boto/boto3`. The backbone holds canonical identifiers built from
published metadata. Resolution is the join between the two, and it is the step
the engine has no answer for at all.

It runs as a pass over the graph rather than inside the ingest pipeline. That
costs nothing and buys two things: no engine source is touched, and the rules
can change without re-extracting. Re-resolving after growing an alias table is
seconds; re-extracting is hours.
"""

from graphpack.resolve.contract import ResolveError, ResolveRules, load_rules
from graphpack.resolve.pipeline import ResolutionReport, resolve_pack

__all__ = [
    "ResolutionReport",
    "ResolveError",
    "ResolveRules",
    "load_rules",
    "resolve_pack",
]
