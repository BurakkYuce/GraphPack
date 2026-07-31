"""Pack contract, loader and validator.

A *pack* is a directory under ``domains/`` that describes one vertical purely as
data.  Nothing in this package knows the name of any particular pack.
"""

from graphpack.packs.contract import Pack, PackError, list_packs, load_pack
from graphpack.packs.ontology import CompiledSchema, OntologyError, compile_ontology

__all__ = [
    "CompiledSchema",
    "OntologyError",
    "Pack",
    "PackError",
    "compile_ontology",
    "list_packs",
    "load_pack",
]
