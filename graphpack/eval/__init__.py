"""Self-labelling evaluation.

The corpus carries its own ground truth. Nobody annotated anything: the backbone
was built from published metadata, so for any pair of packages a document
mentions, the index already says whether one depends on the other. Extraction is
scored against that.

The idea is what makes a second domain cheap. Turkish case law has no annotated
corpus either, and will not get one — but a decision that cites another decision
states the citation in its own text, and that is a gold edge on the same terms.
A pack declares which generator applies; nothing here knows what a package or a
citation is.
"""

from graphpack.eval.contract import EvalError, EvalRules, load_eval_rules
from graphpack.eval.metrics import Scores, score
from graphpack.eval.runner import EvalReport, run_eval

__all__ = [
    "EvalError",
    "EvalReport",
    "EvalRules",
    "Scores",
    "load_eval_rules",
    "run_eval",
    "score",
]
