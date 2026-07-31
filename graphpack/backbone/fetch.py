"""Acquire a pack's raw records over HTTP, once.

Knows about HTTP, JSON and paths into JSON. Not about any particular API — the
endpoints, the fields worth keeping and the request fan-out all come from the
pack's ``sources.yaml``.

Output is JSONL under ``domains/<pack>/data/``, which is gitignored: the graph
is reproducible from a command, so the payload does not belong in git. What is
committed is ``MANIFEST.txt`` — line counts and SHA-256 per file — so a run can
be checked against the one that produced the published numbers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from graphpack.backbone.normalize import field
from graphpack.backbone.sources import FetchSpec, Sources

logger = logging.getLogger(__name__)

USER_AGENT = "GraphPack/0.1 (+https://github.com/BurakkYuce/GraphPack)"
MANIFEST = "MANIFEST.txt"

#: Public APIs are a shared resource. This is slow enough to be unremarkable to
#: the server and fast enough that a thousand requests finish while you watch.
DEFAULT_CONCURRENCY = 8
RETRY_DELAYS = (1.0, 3.0, 8.0)

_URL_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")
_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class FetchError(Exception):
    """Raised when acquisition fails in a way the pack did not allow for."""


@dataclass
class FetchResult:
    spec_id: str
    path: Path
    rows: int
    skipped: int = 0

    def __str__(self) -> str:
        suffix = f", {self.skipped} skipped" if self.skipped else ""
        return f"{self.spec_id}: {self.rows} rows -> {self.path.name}{suffix}"


# ----------------------------------------------------------------------
# Running a pack's fetch block
# ----------------------------------------------------------------------


def fetch_all(sources: Sources, data_dir: Path, force: bool = False) -> list[FetchResult]:
    """Run every fetch step in declaration order.

    Steps are skipped when their output already exists: acquisition is the slow,
    externally-visible part of the pipeline and re-running it should be asked
    for, not assumed. ``force`` overrides.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    results = []

    # Fetch and derive steps interleave: a derive step turns one response into
    # the input list for the next request, so they run in the order that makes
    # each step's source available.
    for spec in sources.fetch:
        target = data_dir / spec.out
        if target.exists() and not force:
            rows = sum(1 for _ in _read_jsonl(target))
            logger.info(
                "%s: %s already present (%d rows) — use --force to refetch", spec.id, spec.out, rows
            )
            results.append(FetchResult(spec.id, target, rows))
        else:
            results.append(_run_step(spec, data_dir))

        for derive_spec in sources.derive:
            if derive_spec.source == spec.out:
                results.append(run_derive(derive_spec, data_dir, sources))

    write_manifest(data_dir)
    return results


def run_derive(spec, data_dir: Path, sources: Sources) -> FetchResult:
    """Build one JSONL from another — no network.

    The repository list is implied by the package records already fetched;
    deriving it keeps the two in step and costs nothing. ``unique`` collapses
    the many packages that share a repository down to one row each.
    """
    from graphpack.backbone.normalize import render
    from graphpack.backbone.rows import expand

    source_path = data_dir / spec.source
    if not source_path.exists():
        raise FetchError(f"{spec.id}: '{spec.source}' does not exist — nothing to derive from")

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in expand(_read_jsonl(source_path), spec):
        built = {
            name: render(template, row, sources.pipelines).strip()
            for name, template in spec.fields.items()
        }
        if any(not built.get(name) for name in spec.require):
            continue
        if spec.unique:
            key = built.get(spec.unique, "")
            if key in seen:
                continue
            seen.add(key)
        rows.append(built)
        if spec.limit is not None and len(rows) >= spec.limit:
            break

    target = data_dir / spec.out
    _write_jsonl(target, rows)
    logger.info("%s: %d rows -> %s (derived from %s)", spec.id, len(rows), spec.out, spec.source)
    return FetchResult(spec.id, target, len(rows))


def _run_step(spec: FetchSpec, data_dir: Path) -> FetchResult:
    target = data_dir / spec.out
    if spec.for_each:
        return _fetch_per_row(spec, data_dir, target)
    return _fetch_once(spec, target)


def _fetch_once(spec: FetchSpec, target: Path) -> FetchResult:
    """Single request whose body holds the whole record set."""
    logger.info("%s: GET %s", spec.id, spec.url)
    payload = _get_json(spec.url, _resolve_headers(spec))
    records = _records_from(payload, spec)
    _write_jsonl(target, records)
    rows = sum(1 for _ in _read_jsonl(target))
    logger.info("%s: %d rows -> %s", spec.id, rows, target.name)
    return FetchResult(spec.id, target, rows)


def _fetch_per_row(spec: FetchSpec, data_dir: Path, target: Path) -> FetchResult:
    """One request per row of a previous step's output."""
    input_path = data_dir / spec.for_each
    if not input_path.exists():
        raise FetchError(
            f"{spec.id}: '{spec.for_each}' does not exist — run the fetch step that produces it first"
        )
    rows = list(_read_jsonl(input_path))
    if spec.limit is not None:
        rows = rows[: spec.limit]
    concurrency = max(1, spec.concurrency or DEFAULT_CONCURRENCY)
    headers = _resolve_headers(spec)
    logger.info("%s: %d requests, %d at a time", spec.id, len(rows), concurrency)

    collected: list[dict[str, Any]] = []
    skipped = 0

    def _one(row: dict[str, Any]) -> list[dict[str, Any]] | None:
        url = _URL_PLACEHOLDER.sub(lambda m: str(field(row, m.group(1)) or ""), spec.url)
        try:
            payload = _get_json(url, headers)
        except FetchError as exc:
            if spec.skips_errors:
                logger.debug("%s: skipping %s — %s", spec.id, url, exc)
                return None
            raise
        # A response may be one record or a collection of them: asking a
        # repository for its issues returns many. Each element becomes a row,
        # merged with the request's own row so the templates that produced the
        # URL can still be referenced downstream.
        selected = field(payload, spec.select) if spec.select else payload
        if isinstance(selected, list):
            return [{**row, **_project(item, spec.keep)} for item in selected]
        return [{**row, **_project(selected, spec.keep)}]

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for index, result in enumerate(pool.map(_one, rows), start=1):
            if result is None:
                skipped += 1
            else:
                collected.extend(result)
            if index % 50 == 0:
                logger.info(
                    "%s: %d/%d requests, %d rows", spec.id, index, len(rows), len(collected)
                )

    _write_jsonl(target, collected)
    logger.info("%s: %d rows -> %s (%d skipped)", spec.id, len(collected), target.name, skipped)
    return FetchResult(spec.id, target, len(collected), skipped)


def _resolve_headers(spec: FetchSpec) -> dict[str, str]:
    """Expand ``${VAR}`` in the pack's declared headers.

    Credentials live in the environment, never in the pack. A variable that is
    not set drops its header rather than sending the literal ``${VAR}`` — an
    unauthenticated request usually still works, at a lower rate limit, and a
    401 is a clearer symptom than a malformed token.
    """
    resolved = {}
    for name, template in spec.headers.items():
        value = _ENV_PLACEHOLDER.sub(lambda m: os.getenv(m.group(1), ""), template)
        if _ENV_PLACEHOLDER.search(template) and not value.strip():
            logger.warning(
                "%s: header '%s' needs an environment variable that is not set — sending without it",
                spec.id,
                name,
            )
            continue
        resolved[name] = value
    return resolved


def _records_from(payload: Any, spec: FetchSpec) -> list[dict[str, Any]]:
    data = field(payload, spec.select) if spec.select else payload
    if data is None:
        raise FetchError(f"{spec.id}: '{spec.select}' not found in the response")
    if not isinstance(data, list):
        raise FetchError(
            f"{spec.id}: expected a list at '{spec.select or 'the response root'}', "
            f"got {type(data).__name__}"
        )
    if spec.limit is not None:
        data = data[: spec.limit]
    records = [row if isinstance(row, dict) else {"value": row} for row in data]
    return [_project(row, spec.keep) for row in records] if spec.keep else records


def _project(payload: Any, keep: dict[str, str]) -> dict[str, Any]:
    """Trim a response down to the fields the pack asked for.

    Responses here run to megabytes while the useful part is a few hundred
    bytes; keeping only what was declared means the stored payload stays small
    and reviewable.
    """
    if not keep:
        return payload if isinstance(payload, dict) else {"value": payload}
    return {name: field(payload, path) for name, path in keep.items()}


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------


def _get_json(url: str, extra_headers: dict[str, str] | None = None) -> Any:
    """GET with a named User-Agent and a short retry ladder.

    Retries only transient failures. A 404 is an answer — for a per-row fetch it
    usually means the row named something that no longer exists — so it fails
    immediately and the pack's ``on_error`` decides whether that ends the run.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, headers=headers)
    last: Exception | None = None

    for attempt, delay in enumerate((0.0, *RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 403, 410):
                raise FetchError(f"{url}: HTTP {exc.code}") from exc
            last = exc
            logger.debug("%s: HTTP %s (attempt %d)", url, exc.code, attempt + 1)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            logger.debug("%s: %s (attempt %d)", url, exc, attempt + 1)

    raise FetchError(f"{url}: giving up after {len(RETRY_DELAYS) + 1} attempts — {last}")


# ----------------------------------------------------------------------
# JSONL and the manifest
# ----------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise FetchError(f"{path}:{number}: not valid JSON — {exc}") from exc


read_jsonl = _read_jsonl


def write_manifest(data_dir: Path) -> Path:
    """Record line counts and digests for every JSONL file in *data_dir*.

    The payload is gitignored; this is what gets committed, so a later run can
    be compared against the one behind the published numbers.
    """
    lines = [
        "# Fetched payloads are gitignored. This records what a run produced,",
        "# so published node and edge counts can be traced to specific inputs.",
        "#",
        "# rows  sha256                                                            file",
    ]
    for path in sorted(data_dir.glob("*.jsonl")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = sum(1 for _ in _read_jsonl(path))
        lines.append(f"{rows:>6}  {digest}  {path.name}")

    manifest = data_dir / MANIFEST
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest
