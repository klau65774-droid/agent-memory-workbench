"""Eligibility before relevance, then relevance × weight × recency. Zero LLM (ADR-002).

The default surface holds active files only. `--as-of` is the one reader of the history
surface, and it judges a file by its validity interval, never by its text alone.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
import pathlib
import sqlite3
from collections.abc import Iterable

from . import observation, timestamp
from .access_log import KIND_RECALL, AccessEntry, AccessLog
from .config import Config
from .database import SURFACE_ACTIVE, SURFACE_HISTORY
from .poison_guard import SafetyReport, inspect_memory_safety
from .record import MemoryRecord
from .redaction import redact
from .search_index import LINK_SEPARATOR, Candidate, SearchIndex
from .store import Store
from .vector_index import VectorIndex

RRF_K = 60
RRF_SOURCE_COUNT = 2
_ACCESS_BOOST_CAP = 0.35
_ACCESS_BOOST_FACTOR = 0.08


def fuse_candidates(lexical: list[Candidate], dense: list[Candidate], pool: int) -> list[Candidate]:
    """Reciprocal-rank fusion by chunk identity, normalized to a stable 0..1 scale."""

    def identity(item: Candidate) -> tuple[str, str, str, str]:
        return (item.name, item.kind, item.anchor, item.heading)

    scores: dict[tuple[str, str, str, str], float] = {}
    exemplars: dict[tuple[str, str, str, str], Candidate] = {}
    for candidates in (lexical, dense):
        seen: set[tuple[str, str, str, str]] = set()
        for rank, candidate in enumerate(candidates, 1):
            key = identity(candidate)
            if key in seen:
                continue
            seen.add(key)
            exemplars.setdefault(key, candidate)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    maximum = RRF_SOURCE_COUNT / (RRF_K + 1)
    fused = [
        dataclasses.replace(exemplars[key], relevance=score / maximum)
        for key, score in scores.items()
    ]
    fused.sort(key=lambda item: (-item.relevance, identity(item)))
    return fused[:pool]


@dataclasses.dataclass(frozen=True)
class Hit:
    name: str
    path: str
    abstract: str
    anchor: str
    heading: str
    type: str
    updated: str
    status: str
    weight: float
    relevance: float
    recency: float
    score: float
    source: str = "memory"
    provenance: tuple[str, ...] = ()
    cited_by: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        payload = dataclasses.asdict(self)
        payload["provenance"] = list(self.provenance)
        payload["cited_by"] = list(self.cited_by)
        return payload


def access_boost(count: int) -> float:
    """Diminishing consolidation gain from past recalls and reads."""
    return 1.0 + min(_ACCESS_BOOST_CAP, _ACCESS_BOOST_FACTOR * math.log1p(count))


@dataclasses.dataclass(frozen=True)
class EnhancedHit:
    hit: Hit
    access_boost: float
    safety: SafetyReport


@dataclasses.dataclass(frozen=True)
class EnhancedRecall:
    hits: list[EnhancedHit]
    query_tokens: list[str]
    quarantined_filtered: int
    visible_names: frozenset[str]


class Recall:
    def __init__(self, store: Store):
        self._store = store
        self._config: Config = store.config

    def recall(
        self,
        query: str,
        scope: str | None = None,
        as_of: str | None = None,
        limit: int | None = None,
        log: bool = True,
    ) -> list[Hit]:
        limit = self._config.recall.default_limit if limit is None else limit
        if limit < 1:
            raise ValueError("limit must be positive")
        pool = limit * self._config.recall.candidate_pool_multiplier
        scope_path = (
            "/".join(pathlib.PurePath(scope.strip("/\\")).parts) if scope else None
        )
        with self._store.search_connection(log=log) as (connection, tokenizer):
            index = SearchIndex(connection, tokenizer)
            eligible = self._eligible(index.rows(), scope=scope, as_of=as_of)
            candidates = self._candidates(
                connection, index, query, pool, scope_path, as_of, set(eligible)
            )
            hits = self._rank(candidates, eligible, as_of=as_of)
            hits = hits[:limit]
            if not log:
                return hits
            self._log_hits(AccessLog(connection), query, hits)
        observation.emit(
            "recall_return", query=query, scope=scope, as_of=as_of,
            effective_limit=limit, hits=[hit.as_dict() for hit in hits],
        )
        return hits

    def recall_enhanced(
        self,
        query: str,
        scope: str | None = None,
        as_of: str | None = None,
        limit: int | None = None,
        *,
        include_quarantined: bool = False,
        log: bool = True,
        records: Iterable[MemoryRecord] | None = None,
    ) -> EnhancedRecall:
        limit = self._config.recall.default_limit if limit is None else limit
        if limit < 1:
            raise ValueError("limit must be positive")
        moment = timestamp.parse(as_of) if as_of is not None else self._store.clock.now()
        effective_as_of = moment.isoformat()
        truth = {
            record.name: record
            for record in (
                self._store.records(include_invalid=True) if records is None else records
            )
        }
        pool = min(limit, max(1, len(truth))) * self._config.recall.candidate_pool_multiplier
        scope_path = "/".join(pathlib.PurePath(scope.strip("/\\")).parts) if scope else None

        with self._store.search_connection(log=log) as (connection, tokenizer):
            index = SearchIndex(connection, tokenizer)
            eligible = self._eligible(index.rows(), scope=scope, as_of=effective_as_of)
            reports = {
                name: inspect_memory_safety(truth[name].abstract, truth[name].body)
                for name in eligible.keys() & truth.keys()
            }
            allowed = {
                name for name, report in reports.items()
                if include_quarantined or report.trust_level != "quarantined"
            }
            access_log = AccessLog(connection)
            counts = access_log.counts(through=moment)
            candidates = self._candidates(
                connection, index, query, pool, scope_path, effective_as_of, allowed,
                filter_lexical=True,
            )
            ranked = self._rank(candidates, eligible, effective_as_of)
            hits = []
            for hit in ranked:
                boost = access_boost(counts.get(hit.name, 0))
                adjusted = dataclasses.replace(
                    hit, abstract=truth[hit.name].abstract, score=hit.score * boost
                )
                hits.append(EnhancedHit(adjusted, boost, reports[hit.name]))
            hits.sort(key=lambda item: (-item.hit.score, item.hit.name))
            hits = hits[:limit]
            if log:
                self._log_hits(access_log, redact(query), (item.hit for item in hits))
        return EnhancedRecall(
            hits=hits,
            query_tokens=list(dict.fromkeys(tokenizer.query(query))),
            quarantined_filtered=len(reports) - len(allowed),
            visible_names=frozenset(allowed),
        )

    def _candidates(
        self,
        connection: sqlite3.Connection,
        index: SearchIndex,
        query: str,
        pool: int,
        scope_path: str | None,
        as_of: str | None,
        eligible_names: set[str],
        *,
        filter_lexical: bool = False,
    ) -> list[Candidate]:
        surfaces = (SURFACE_ACTIVE,) if as_of is None else (SURFACE_ACTIVE, SURFACE_HISTORY)
        lexical_names = eligible_names if filter_lexical else None
        candidates = [
            candidate
            for surface in surfaces
            for candidate in index.match(
                query, pool, surface, scope_path=scope_path, eligible_names=lexical_names
            )
        ]
        if self._config.index.vector_enabled:
            assert self._store.embedder is not None
            dense = VectorIndex(
                connection, self._store.embedder, self._config.index.vector_model
            ).match(query, pool, eligible_names=eligible_names)
            if dense:
                candidates = fuse_candidates(candidates, dense, pool)
        return candidates

    def _log_hits(self, access_log: AccessLog, query: str, hits: Iterable[Hit]) -> None:
        recalled_at = self._store.clock.now().isoformat()
        access_log.append(
            [
                AccessEntry(recalled_at, hit.name, query, KIND_RECALL, self._store.agent)
                for hit in hits
            ]
        )

    def _eligible(
        self,
        rows: list[sqlite3.Row],
        scope: str | None,
        as_of: str | None,
    ) -> dict[str, sqlite3.Row]:
        eligible: dict[str, sqlite3.Row] = {}
        moment = timestamp.parse(as_of) if as_of else None
        for row in rows:
            name = str(row["name"])
            if scope and not self._in_scope(str(row["path"]), scope):
                continue
            if moment is None:
                if row["invalid_at"]:
                    continue
            elif not self._current_at(row, moment):
                continue
            eligible[name] = row
        return eligible

    def _in_scope(self, path: str, scope: str) -> bool:
        path_parts = pathlib.PurePath(path).parts
        scope_parts = pathlib.PurePath(scope.strip("/\\")).parts
        return bool(scope_parts) and path_parts[: len(scope_parts)] == scope_parts

    def _current_at(self, row: sqlite3.Row, moment: dt.datetime) -> bool:
        if timestamp.parse(str(row["valid_from"])) > moment:
            return False
        ended = row["invalid_at"]
        return not (ended and timestamp.parse(str(ended)) <= moment)

    def _rank(
        self,
        candidates: list[Candidate],
        eligible: dict[str, sqlite3.Row],
        as_of: str | None,
    ) -> list[Hit]:
        best: dict[str, tuple[float, str, str]] = {}
        for candidate in candidates:
            if candidate.name not in eligible:
                continue
            weighted = candidate.relevance * self._kind_weight(candidate.kind)
            current = best.get(candidate.name)
            if current is None or weighted > current[0]:
                best[candidate.name] = (weighted, candidate.anchor, candidate.heading)

        reference = timestamp.parse(as_of) if as_of else self._store.clock.now()
        hits: list[Hit] = []
        for name, (relevance, anchor, heading) in best.items():
            row = eligible[name]
            recency = self._recency(str(row["updated"]), reference)
            weight = float(row["weight"])
            hits.append(
                Hit(
                    name=name,
                    path=str(self._store.root / str(row["path"])),
                    abstract=str(row["abstract"]),
                    anchor=anchor,
                    heading=heading,
                    type=str(row["type"]),
                    updated=str(row["updated"]),
                    status="invalid" if row["invalid_at"] else "active",
                    weight=weight,
                    relevance=relevance,
                    recency=recency,
                    score=relevance * weight * recency,
                    provenance=tuple(
                        item for item in str(row["provenance"]).split(LINK_SEPARATOR) if item
                    ),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.name))
        return hits

    def _kind_weight(self, kind: str) -> float:
        from .chunking import KIND_ABSTRACT

        if kind == KIND_ABSTRACT:
            return self._config.index.bm25_abstract_weight
        return self._config.index.bm25_body_weight

    def _recency(self, updated: str, reference: dt.datetime) -> float:
        age_days = max(0.0, timestamp.days_between(reference, timestamp.parse(updated)))
        decayed = self._config.recall.recency_decay_base ** (
            age_days / self._config.recall.recency_half_life_days
        )
        return max(self._config.recall.recency_floor, decayed)
