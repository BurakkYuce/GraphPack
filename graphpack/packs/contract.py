"""Pack contract v1 — the declarative shape of ``domains/<pack>/``.

Every file is optional except ``pack.yaml`` and ``ontology.ttl``; the phases that
need the rest declare them as they land (see ``REQUIRED_BY_PHASE``).

    pack.yaml        identity + engine knobs
    ontology.ttl     OWL classes / object properties -> extraction schema
    sources.yaml     structured sources -> backbone nodes and edges   (phase 1)
    resolve.yaml     mention -> canonical id resolution rules          (phase 3)
    eval.yaml        gold generator selection                          (phase 4)
    retrieval.yaml   intent -> Cypher template                         (phase 6)
    aliases.csv      alias table grown from resolution error analysis  (phase 3)
    questions.jsonl  QA set for the agent runner                       (phase 6)
    checks.cypher    hand-written sanity queries                       (phase 1)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from graphpack.paths import domains_root

CONTRACT_VERSION = 1

#: Files a pack may contain, mapped to the phase that first requires them.
#: ``packs validate`` only insists on the ones for phases already implemented.
REQUIRED_BY_PHASE: dict[str, int] = {
    "pack.yaml": 0,
    "ontology.ttl": 0,
    "sources.yaml": 1,
    "resolve.yaml": 3,
    "eval.yaml": 4,
    "retrieval.yaml": 6,
}

#: Highest phase whose artifacts must be present for validation to pass.
#: Bumped as each phase lands, so a half-built pack fails loudly instead of
#: silently skipping work.
IMPLEMENTED_PHASE = 4

#: Pack names the engine intercepts before it ever looks at our schema list.
#: ``Settings.get_active_schema`` short-circuits on these: "sample" returns the
#: engine's built-in SAMPLE_SCHEMA, the rest return None and fall back to
#: LlamaIndex's internal schema. A pack with one of these names would therefore
#: be ingested under a schema that has nothing to do with its ontology, and
#: nothing in the logs would call it an error.
RESERVED_NAMES = frozenset({"default", "none", "sample"})


class PackError(Exception):
    """Raised when a pack directory does not satisfy the contract."""


@dataclass(frozen=True)
class Pack:
    """One vertical, loaded from ``domains/<name>/``.

    Attributes mirror ``pack.yaml`` one-for-one; no defaults are invented here
    beyond the ones spelled out in :meth:`from_dir`, so a reader of the YAML can
    predict the behaviour without reading this file.
    """

    name: str
    version: str
    root: Path
    lang: str = "en"
    id_prefix: str = ""
    description: str = ""

    # --- extraction knobs -------------------------------------------------
    # strict_schema drives Settings.strict_schema_validation. It is a *pack*
    # knob rather than a constant because the engine's own docs recommend
    # strict=false for recall while type-clean extraction wants strict=true —
    # phase 4 measures both instead of guessing.
    strict_schema: bool = True
    max_triplets_per_chunk: int = 20
    chunk_size: int = 1024
    chunk_overlap: int = 128

    # --- model selection --------------------------------------------------
    # llm_config is passed through to the engine verbatim. When empty the engine
    # derives it from the environment, which is the normal development path.
    llm_provider: str | None = None
    llm_config: dict[str, Any] = field(default_factory=dict)
    embedding_kind: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = None

    # --- store targets ----------------------------------------------------
    qdrant_collection: str = ""

    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def path(self, filename: str) -> Path:
        """Absolute path to a file inside the pack directory."""
        return self.root / filename

    @property
    def ontology_path(self) -> Path:
        return self.path("ontology.ttl")

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    def optional(self, filename: str) -> Path | None:
        """Absolute path if the file exists, else ``None``."""
        p = self.path(filename)
        return p if p.is_file() else None

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def ontology_checksum(self) -> str:
        """SHA-256 of ``ontology.ttl`` — recorded on ``(:_Pack)`` so an ontology
        change that needs a migration cannot pass unnoticed."""
        return hashlib.sha256(self.ontology_path.read_bytes()).hexdigest()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_dir(cls, root: Path) -> Pack:
        root = root.resolve()
        manifest = root / "pack.yaml"
        if not manifest.is_file():
            raise PackError(f"{root}: pack.yaml missing")

        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise PackError(f"{manifest}: invalid YAML — {exc}") from exc
        if not isinstance(data, dict):
            raise PackError(f"{manifest}: top level must be a mapping")

        name = str(data.get("name") or "").strip()
        if not name:
            raise PackError(f"{manifest}: 'name' is required")
        if name != root.name:
            raise PackError(f"{manifest}: name '{name}' does not match directory '{root.name}'")
        if name.lower() in RESERVED_NAMES:
            raise PackError(
                f"{manifest}: '{name}' is reserved by the engine — "
                "Settings.get_active_schema intercepts it and would silently ingest "
                f"this pack under a different schema. Reserved: {', '.join(sorted(RESERVED_NAMES))}."
            )
        version = str(data.get("version") or "").strip()
        if not version:
            raise PackError(f"{manifest}: 'version' is required")

        extraction = _mapping(data, "extraction", manifest)
        llm = _mapping(data, "llm", manifest)
        embedding = _mapping(data, "embedding", manifest)
        stores = _mapping(data, "stores", manifest)

        qdrant_collection = str(stores.get("qdrant_collection") or f"{_slug(name)}_chunks")

        return cls(
            name=name,
            version=version,
            root=root,
            lang=str(data.get("lang") or "en"),
            id_prefix=str(data.get("id_prefix") or ""),
            description=str(data.get("description") or ""),
            strict_schema=bool(extraction.get("strict_schema", True)),
            max_triplets_per_chunk=int(extraction.get("max_triplets_per_chunk", 20)),
            chunk_size=int(extraction.get("chunk_size", 1024)),
            chunk_overlap=int(extraction.get("chunk_overlap", 128)),
            llm_provider=_opt_str(llm.get("provider")),
            llm_config=dict(_mapping(llm, "config", manifest)),
            embedding_kind=_opt_str(embedding.get("kind")),
            embedding_model=_opt_str(embedding.get("model")),
            embedding_dimension=_opt_int(embedding.get("dimension")),
            qdrant_collection=qdrant_collection,
            raw=data,
        )


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------


def list_packs(root: Path | None = None) -> list[str]:
    """Names of every pack directory, sorted. A directory counts as a pack when
    it holds a ``pack.yaml``."""
    base = root or domains_root()
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if (d / "pack.yaml").is_file())


def load_pack(name: str, root: Path | None = None) -> Pack:
    base = root or domains_root()
    candidate = base / name
    if not candidate.is_dir():
        known = ", ".join(list_packs(base)) or "none"
        raise PackError(f"pack '{name}' not found under {base} (known packs: {known})")
    return Pack.from_dir(candidate)


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------


def _mapping(data: dict[str, Any], key: str, manifest: Path) -> dict[str, Any]:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise PackError(f"{manifest}: '{key}' must be a mapping, got {type(value).__name__}")
    return value


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _slug(name: str) -> str:
    """Pack name as a store-safe identifier (Qdrant collections, index names)."""
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
