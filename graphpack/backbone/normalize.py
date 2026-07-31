"""String operations packs compose by name, and the templates that use them.

Deliberately generic. A pack that needs PEP 503 package-name normalisation
writes it out as ``lower`` plus a regex replace rather than calling an op named
after the specification — the moment an op knows what a Python package is, the
"no domain knowledge in code" claim stops being true.

Templates look like ``"pypi:{name|pypi_name}"``: a literal prefix, a field
reference, and an optional pipeline of named normalisers.

Several fields may be offered for one slot — ``{repo_url,code_url|repo_slug}``
takes the first that yields something after normalising. Real records put the
same fact in whichever field the publisher happened to fill in, and the
alternative is one load step per candidate field.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: ``{field}``, ``{field|pipeline}`` or ``{first,second|pipeline}``.
#: Field names may be dotted to reach into nested records.
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.,]+)(?:\|([A-Za-z0-9_]+))?\}")


class NormalizeError(Exception):
    """Raised when a pack declares an operation that does not exist or is malformed."""


Op = Callable[[str], str]


def _op_lower(_: Any) -> Op:
    return str.lower


def _op_upper(_: Any) -> Op:
    return str.upper


def _op_strip(_: Any) -> Op:
    return str.strip


def _op_regex_replace(config: Any) -> Op:
    if not isinstance(config, dict) or "pattern" not in config:
        raise NormalizeError("regex_replace needs a mapping with 'pattern' and 'replace'")
    pattern = re.compile(config["pattern"])
    replacement = str(config.get("replace", ""))
    return lambda value: pattern.sub(replacement, value)


def _op_regex_extract(config: Any) -> Op:
    """First capture group of the first match, or the empty string.

    Returning empty rather than raising is what lets a pack use extraction as a
    filter: rows whose id renders empty are dropped by the loader.
    """
    if not isinstance(config, dict) or "pattern" not in config:
        raise NormalizeError("regex_extract needs a mapping with 'pattern'")
    pattern = re.compile(config["pattern"])

    def _extract(value: str) -> str:
        match = pattern.search(value)
        if not match:
            return ""
        return match.group(1) if match.groups() else match.group(0)

    return _extract


#: Every operation a pack may name. Adding one is a deliberate widening of the
#: vocabulary available to every pack, not a favour to one of them.
BUILDERS: dict[str, Callable[[Any], Op]] = {
    "lower": _op_lower,
    "upper": _op_upper,
    "strip": _op_strip,
    "regex_replace": _op_regex_replace,
    "regex_extract": _op_regex_extract,
}


@dataclass(frozen=True)
class Pipeline:
    """A named sequence of operations, applied left to right."""

    name: str
    ops: tuple[Op, ...]

    def __call__(self, value: str) -> str:
        for op in self.ops:
            value = op(value)
        return value


def build_pipelines(declared: dict[str, Any] | None) -> dict[str, Pipeline]:
    """Compile a pack's ``normalize:`` block into callables.

    Each entry is a list whose items are either a bare op name or a single-key
    mapping of op name to configuration::

        normalize:
          pypi_name:
            - lower
            - {regex_replace: {pattern: "[-_.]+", replace: "-"}}
    """
    pipelines: dict[str, Pipeline] = {}
    for name, steps in (declared or {}).items():
        if not isinstance(steps, list):
            raise NormalizeError(f"normalize.{name} must be a list of operations")
        ops: list[Op] = []
        for step in steps:
            if isinstance(step, str):
                op_name, config = step, None
            elif isinstance(step, dict) and len(step) == 1:
                op_name, config = next(iter(step.items()))
            else:
                raise NormalizeError(
                    f"normalize.{name}: each step is an op name or a single-key mapping, got {step!r}"
                )
            builder = BUILDERS.get(op_name)
            if builder is None:
                raise NormalizeError(
                    f"normalize.{name}: unknown operation '{op_name}'. "
                    f"Available: {', '.join(sorted(BUILDERS))}"
                )
            ops.append(builder(config))
        pipelines[name] = Pipeline(name=name, ops=tuple(ops))
    return pipelines


# ----------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------


def field(row: dict[str, Any], path: str) -> Any:
    """Read a dotted path out of a nested mapping, ``None`` when absent."""
    current: Any = row
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def render(template: str, row: dict[str, Any], pipelines: dict[str, Pipeline]) -> str:
    """Substitute ``{field|pipeline}`` placeholders from *row*."""
    return render_parts(template, row, pipelines)[0]


def render_parts(
    template: str, row: dict[str, Any], pipelines: dict[str, Pipeline]
) -> tuple[str, bool]:
    """Render *template*, and report whether every placeholder found a value.

    The second element is what callers building identifiers need. A template
    like ``"gh:{repo}#{number}"`` on a row with no number renders
    ``"gh:psf/requests#"`` — a perfectly non-empty string that silently collides
    with every other numberless row from the same repository. Inspecting the
    output cannot distinguish that from a legitimate id; knowing that a
    placeholder came back empty can.

    Rows are external data and half-complete records are normal, so an unfilled
    placeholder is reported rather than raised: the caller decides whether this
    row simply has no such node, edge or document.
    """
    complete = True

    def _substitute(match: re.Match[str]) -> str:
        nonlocal complete
        paths, pipeline_name = match.group(1).split(","), match.group(2)
        pipeline = None
        if pipeline_name:
            pipeline = pipelines.get(pipeline_name)
            if pipeline is None:
                raise NormalizeError(
                    f"template '{template}' uses undefined normalize pipeline '{pipeline_name}'"
                )

        for path in paths:
            value = field(row, path.strip())
            if value is None:
                continue
            text = str(value)
            if pipeline:
                text = pipeline(text)
            # Normalising can empty a value out — a homepage that is not a
            # repository URL survives the field lookup but not the extraction.
            # Only then is the next candidate worth trying.
            if text:
                return text
        complete = False
        return ""

    return _PLACEHOLDER.sub(_substitute, template), complete


def referenced_pipelines(template: str) -> set[str]:
    """Pipeline names a template mentions — used to validate a pack statically."""
    return {m.group(2) for m in _PLACEHOLDER.finditer(template) if m.group(2)}
