"""Run a pack's hand-written sanity queries.

Loading without looking is how a graph ends up confidently wrong: the counts are
plausible, the queries return rows, and a normaliser has been quietly dropping
half the edges. ``checks.cypher`` is where a pack states what it expects to see.

Queries titled ``[must be empty]`` are assertions and fail the run when they
return anything. The rest are printed for a human to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: A title line marked this way turns its query into an assertion.
_MUST_BE_EMPTY = "[must be empty]"

_TITLE = re.compile(r"^//\s*(?P<title>.+?)\s*$")


class CheckError(Exception):
    """Raised when checks.cypher cannot be parsed."""


@dataclass(frozen=True)
class Check:
    title: str
    query: str
    must_be_empty: bool


@dataclass
class CheckResult:
    check: Check
    rows: list[dict[str, Any]]

    @property
    def failed(self) -> bool:
        return self.check.must_be_empty and bool(self.rows)


def parse_checks(path: Path) -> list[Check]:
    """Split ``checks.cypher`` into titled queries.

    A query runs from its `//` title block to the next semicolon. Comment lines
    between the title and the statement are commentary for the reader and are
    dropped before execution.
    """
    if not path.is_file():
        raise CheckError(f"{path}: no checks.cypher in this pack")

    checks: list[Check] = []
    title_lines: list[str] = []
    statement: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            if not statement:
                title_lines = []
            continue
        if line.startswith("//"):
            if not statement:
                match = _TITLE.match(line)
                if match:
                    title_lines.append(match.group("title"))
            continue

        statement.append(line)
        if line.endswith(";"):
            query = " ".join(statement).rstrip(";").strip()
            title = title_lines[0] if title_lines else query[:60]
            # Only the first line is the title, so a marker further down would
            # quietly leave the query as commentary — an assertion that never
            # asserts, which is worse than not writing one.
            for later in title_lines[1:]:
                if _MUST_BE_EMPTY in later:
                    raise CheckError(
                        f"{path}: '{title.replace(_MUST_BE_EMPTY, '').strip()}' has "
                        f"{_MUST_BE_EMPTY} on a later comment line, where it does "
                        "nothing. Put it on the first line of the block."
                    )
            checks.append(
                Check(
                    title=title.replace(_MUST_BE_EMPTY, "").strip(),
                    query=query,
                    must_be_empty=_MUST_BE_EMPTY in title,
                )
            )
            statement, title_lines = [], []

    if statement:
        raise CheckError(f"{path}: last query is missing its closing semicolon")
    if not checks:
        raise CheckError(f"{path}: no queries found")
    return checks


def run_checks(session, path: Path) -> list[CheckResult]:
    """Execute every check and collect its rows."""
    results = []
    for check in parse_checks(path):
        rows = [dict(record) for record in session.run(check.query)]
        results.append(CheckResult(check=check, rows=rows))
    return results
