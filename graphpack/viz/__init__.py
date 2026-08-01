"""Drawing a run.

The trace records which nodes and edges each step touched; this turns that into
a page that replays them. Self-contained HTML with nothing external, because a
visualisation that needs a build step or a network request is one nobody looks
at twice.
"""

from graphpack.viz.render import render_page
from graphpack.viz.subgraph import Subgraph, subgraph_for

__all__ = ["Subgraph", "render_page", "subgraph_for"]
