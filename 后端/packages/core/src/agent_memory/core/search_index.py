"""BM25 over abstract + body (ADR-003). Projection only: never a source of truth."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Collection
from functools import cached_property

from .chunking import Chunk
from .database import SURFACE_ACTIVE, SURFACE_HISTORY
from .record import MemoryRecord
from .tokenization import (
    TOKENIZER_ASCII,
    TOKENIZER_HYBRID,
    Tokenizer,
    validate_tokenizer,
)

LINK_SEPARATOR = ","
SURFACES = (SURFACE_ACTIVE, SURFACE_HISTORY)


@dataclasses.dataclass(frozen=True)
class Candidate:
    name: str
    kind: str
    anchor: str
    heading: str
    relevance: float


def to_match_query(query: str, tokenizer: str | Tokenizer = TOKENIZER_ASCII) -> str:
    segmenter = Tokenizer(tokenizer) if isinstance(tokenizer, str) else tokenizer
    tokens = segmenter.query(query)
    return " OR ".join(f'"{token}"' for token in dict.fromkeys(tokens))


class SearchIndex:
    def __init__(
        self, connection: sqlite3.Connection, tokenizer: str | Tokenizer = TOKENIZER_ASCII
    ):
        if isinstance(tokenizer, str):
            validate_tokenizer(tokenizer)
        self._connection = connection
        self._tokenizer = tokenizer

    @cached_property
    def segmenter(self) -> Tokenizer:
        return Tokenizer(self._tokenizer) if isinstance(self._tokenizer, str) else self._tokenizer

    def _project_text(self, chunk: Chunk) -> str:
        body = self.segmenter.segment(chunk.text)
        if self.segmenter.name != TOKENIZER_HYBRID:
            return body
        heading = self.segmenter.segment(chunk.heading)
        # The heading column also supplies the display title, so keep it verbatim.
        return body if heading == chunk.heading else f"{body} {heading}"

    def upsert(self, record: MemoryRecord, chunks: list[Chunk], relative_path: str) -> None:
        self.remove(record.name)
        self._connection.execute(
            "INSERT INTO records(name, path, type, abstract, created, updated, "
            "valid_from, invalid_at, superseded_by, weight, author, links, provenance) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.name,
                relative_path,
                record.type,
                record.abstract,
                record.created,
                record.updated,
                record.valid_from or record.created,
                record.invalid_at,
                record.superseded_by,
                float(record.weight),
                record.author,
                LINK_SEPARATOR.join(record.links),
                LINK_SEPARATOR.join(record.provenance),
            ),
        )
        surface = SURFACE_ACTIVE if record.is_active() else SURFACE_HISTORY
        self._connection.executemany(
            f"INSERT INTO {surface}(name, kind, anchor, heading, text) VALUES(?, ?, ?, ?, ?)",
            [
                (
                    record.name,
                    chunk.kind,
                    chunk.anchor,
                    chunk.heading,
                    self._project_text(chunk),
                )
                for chunk in chunks
            ],
        )

    def remove(self, name: str) -> None:
        self._connection.execute("DELETE FROM records WHERE name = ?", (name,))
        for surface in SURFACES:
            self._connection.execute(f"DELETE FROM {surface} WHERE name = ?", (name,))

    def remove_path(self, path: str) -> None:
        rows = self._connection.execute(
            "SELECT name FROM records WHERE path = ?", (path,)
        ).fetchall()
        for row in rows:
            self.remove(row["name"])

    def rows(self, include_invalid: bool = True) -> list[sqlite3.Row]:
        if include_invalid:
            return self._connection.execute("SELECT * FROM records").fetchall()
        return self._connection.execute("SELECT * FROM records WHERE invalid_at IS NULL").fetchall()

    def row(self, name: str) -> sqlite3.Row | None:
        return self._connection.execute("SELECT * FROM records WHERE name = ?", (name,)).fetchone()

    def match(
        self,
        query: str,
        pool: int,
        surface: str = SURFACE_ACTIVE,
        *,
        scope_path: str | None = None,
        eligible_names: Collection[str] | None = None,
    ) -> list[Candidate]:
        if surface not in SURFACES:
            raise ValueError(f"unknown surface {surface}")
        expression = to_match_query(query, self.segmenter)
        if not expression or scope_path == "":
            return []
        if eligible_names is not None and not eligible_names:
            return []
        statement = (
            f"SELECT {surface}.name, kind, anchor, heading, bm25({surface}) AS rank FROM {surface}"
        )
        where = [f"{surface} MATCH ?"]
        parameters: list[object] = [expression]
        if scope_path is not None:
            prefix = scope_path + "/"
            statement += f" JOIN records ON records.name = {surface}.name"
            where.append("(records.path = ? OR substr(records.path, 1, ?) = ?)")
            parameters.extend((scope_path, len(prefix), prefix))
        if eligible_names is not None:
            where.append(f"{surface}.name IN (SELECT value FROM json_each(?))")
            parameters.append(json.dumps(sorted(eligible_names)))
        statement += " WHERE " + " AND ".join(where) + " ORDER BY rank LIMIT ?"
        parameters.append(pool)
        rows = self._connection.execute(statement, parameters).fetchall()
        return [
            Candidate(
                name=row["name"],
                kind=row["kind"],
                anchor=row["anchor"],
                heading=row["heading"],
                relevance=-float(row["rank"]),
            )
            for row in rows
        ]
