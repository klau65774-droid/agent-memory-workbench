"""The read surface that decides disclosure itself.

`recall` hands back an L0 list and leaves the ladder to the caller: search again, or open an
entry, or stop. That delegation is the design's intent for an agent that knows what it is
looking for, and it is measurably expensive for one that does not — an agent driving its own
retrieval swings the same store's answers by seven per hundred between identical replays, and
scores well below the same store read on a fixed policy (P5/fixed, docs in experiments/).

So the ladder is also available as one call: the store runs the recall, opens the top entries
to full text, and returns a context block ready to be reasoned over. Same retrieval, same
scoring, same eligibility — the only thing that moves inside is who decides when to stop.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable
from typing import Literal

from . import chunking
from .config import Config
from .poison_guard import wrap_with_spotlighting
from .recall import EnhancedHit, Recall
from .record import MemoryRecord
from .store import LEVEL_FULL, Store

HEADER = "Entries from your memory store, most relevant first:"
NOTHING_FOUND = "Your memory store returned nothing for this question."
ENTRY_SEPARATOR = "\n\n"
BULLET = "- "
_TOKEN_PARTS = re.compile(r"([\u4e00-\u9fff])|([A-Za-z0-9_]+)|(\S)")
# Costs in twentieths of a token: 1.5 for Chinese, 1.3 for a word, 0.25 for other symbols.
_TOKEN_COST = (0, 30, 26, 5)
_TOKEN_UNIT = 20
_PERCENT = 100
type DisclosureLevel = Literal["abstract", "outline", "full"]


@dataclasses.dataclass(frozen=True)
class Context:
    text: str
    entries: int
    names: tuple[str, ...]

    def is_empty(self) -> bool:
        return self.entries == 0


@dataclasses.dataclass(frozen=True)
class Disclosure:
    abstract: str
    outline: str
    full: str

    def at(self, level: DisclosureLevel) -> str:
        return getattr(self, level)


@dataclasses.dataclass(frozen=True)
class TokenBudget:
    full_store_tokens: int
    progressive_context_tokens: int
    tokens_saved: int
    savings_pct: float


def estimate_tokens(text: str) -> int:
    cost = sum(_TOKEN_COST[match.lastindex or 0] for match in _TOKEN_PARTS.finditer(text))
    return (cost + _TOKEN_UNIT - 1) // _TOKEN_UNIT


def savings_pct(baseline: int, used: int) -> float:
    return round(_PERCENT * (baseline - used) / baseline, 1) if baseline else 0.0


def disclose(record: MemoryRecord, config: Config) -> Disclosure:
    outline = "\n".join(
        f"{'#' * item.level} {item.title}" for item in chunking.outline(record.body, config)
    )
    return Disclosure(record.abstract, outline, record.body or record.abstract)


def build_progressive(
    store: Store,
    hits: Iterable[EnhancedHit],
    records: Iterable[MemoryRecord],
    *,
    spotlighting: bool = True,
    level: Literal["abstract", "outline"] = "outline",
) -> tuple[Context, TokenBudget]:
    """Compare the actual preview to the full corpus supplied by the caller."""
    truth = {record.name: record for record in records}
    rendered: list[str] = []
    names: list[str] = []
    for position, item in enumerate(hits):
        record = truth[item.hit.name]
        text = BULLET + record.abstract
        if position == 0 and level == "outline":
            outline = disclose(record, store.config).outline
            if outline:
                text += "\n" + outline
        if spotlighting:
            text = wrap_with_spotlighting(text, item.safety.trust_level)
        rendered.append(text)
        names.append(record.name)

    preview = (
        HEADER + ENTRY_SEPARATOR + ENTRY_SEPARATOR.join(rendered) if rendered else NOTHING_FOUND
    )
    full = (
        HEADER + ENTRY_SEPARATOR + ENTRY_SEPARATOR.join(
            _entry(record.abstract, record.body) for record in truth.values()
        )
        if truth else NOTHING_FOUND
    )
    baseline = estimate_tokens(full)
    used = estimate_tokens(preview)
    return Context(preview, len(names), tuple(names)), TokenBudget(
        baseline, used, baseline - used, savings_pct(baseline, used)
    )


def build(
    store: Store,
    query: str,
    scope: str | None = None,
    as_of: str | None = None,
    limit: int | None = None,
) -> Context:
    hits = Recall(store).recall(query, scope=scope, as_of=as_of, limit=limit)
    if not hits:
        return Context(text=NOTHING_FOUND, entries=0, names=())

    full_text_entries = store.config.recall.context_full_text_entries
    rendered = [
        _entry(hit.abstract, _body(store, hit.name) if position < full_text_entries else "")
        for position, hit in enumerate(hits)
    ]
    return Context(
        text=HEADER + ENTRY_SEPARATOR + ENTRY_SEPARATOR.join(rendered),
        entries=len(hits),
        names=tuple(hit.name for hit in hits),
    )


def _entry(abstract: str, body: str) -> str:
    head = BULLET + abstract
    return f"{head}\n{body}" if body.strip() else head


def _body(store: Store, name: str) -> str:
    try:
        return store.read(name, level=LEVEL_FULL).text
    except Exception:
        return ""
