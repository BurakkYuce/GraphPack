"""One event loop for the process.

The engine's clients — Qdrant's async client, the Ollama and Gemini HTTP
sessions — bind to the loop they were first used on. `asyncio.run` creates a
loop and closes it on the way out, so anything reaching those clients afterwards
dies with "Event loop is closed".

This cost a whole benchmark once: `asyncio.run` per query meant every query
after the first failed, and 2,255 of them reported a retrieval score of exactly
0.000. Queries were fixed then; ingest still had its own `asyncio.run`, which
did not matter while ingest and query were always separate processes and started
mattering the moment one command did both.

Living here rather than beside the agent because it is a property of the
process, not of any one stage.
"""

from __future__ import annotations

import asyncio

_LOOP: asyncio.AbstractEventLoop | None = None


def run(coroutine):
    """Run a coroutine on this process's one long-lived loop."""
    global _LOOP

    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_LOOP)
    return _LOOP.run_until_complete(coroutine)
