"""Truth → projection. Incremental by content hash; a full rebuild is the same code path."""

from __future__ import annotations

import dataclasses
import pathlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from . import chunking
from . import record as record_module
from .clock import Clock
from .database import SURFACE_ACTIVE, SURFACE_HISTORY, Database
from .embeddings import Embedder
from .errors import ValidationError
from .locking import store_lock
from .manifest import CHUNKING_KEY, VECTOR_MODEL_KEY, IndexMeta, Manifest, content_hash
from .paths import StoreLayout
from .record import MemoryRecord
from .schema import SchemaRegistry
from .search_index import SearchIndex
from .tokenization import Tokenizer
from .vector_index import VectorIndex


@dataclasses.dataclass(frozen=True)
class IndexReport:
    reindexed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    dangling_links: tuple[tuple[str, str], ...] = ()

    def is_empty(self) -> bool:
        return not (self.reindexed or self.removed)


class Indexer:
    def __init__(
        self, layout: StoreLayout, clock: Clock | None = None, embedder: Embedder | None = None
    ):
        self._layout = layout
        self._config = layout.config
        self._clock = clock or Clock()
        self._database = Database(layout)
        self._schemas = SchemaRegistry(layout)
        self._embedder = embedder

    def sync(self, *, rebuild: bool = False) -> IndexReport:
        tokenizer = self._tokenizer()
        chunk_signature = self._chunk_signature()
        present = self._present_hashes()
        with self._database.connect() as connection:
            manifest = Manifest(connection, self._config.index.hash_prefix_length)
            index = SearchIndex(connection, tokenizer)
            metadata = IndexMeta(connection)
            chunks_changed = metadata.get(CHUNKING_KEY) != chunk_signature
            if rebuild or chunks_changed or metadata.tokenizer() != tokenizer.signature:
                # Replace the projection in one transaction, retaining usage telemetry.
                for table in ("files", "records", SURFACE_ACTIVE, SURFACE_HISTORY):
                    connection.execute(f"DELETE FROM {table}")
                if rebuild or chunks_changed:
                    connection.execute("DELETE FROM vector_files")
                    connection.execute("DELETE FROM vector_chunks")
                metadata.set_tokenizer(tokenizer.signature)
                metadata.set(CHUNKING_KEY, chunk_signature)
            delta = manifest.diff(present)
            unreadable: list[str] = []
            reindexed: list[str] = []
            for relative in delta.touched:
                path = self._layout.root / relative
                record = self._load(path)
                index.remove_path(relative)
                if record is None:
                    manifest.forget(relative)
                    unreadable.append(relative)
                    continue
                index.upsert(record, chunking.chunks(record, self._config), relative)
                manifest.record(
                    relative, record.name, present[relative], self._clock.now().isoformat()
                )
                reindexed.append(relative)
            for relative in delta.removed:
                index.remove_path(relative)
                manifest.forget(relative)
            if self._config.index.vector_enabled:
                assert self._embedder is not None
                self._sync_vectors(connection, present, self._embedder)
                metadata.set(VECTOR_MODEL_KEY, self._config.index.vector_model)
            elif rebuild or chunks_changed or delta.touched or delta.removed:
                # A later vector-enabled reader must validate its paths, digests and model.
                metadata.set(VECTOR_MODEL_KEY, "")
            dangling = self._dangling_links(index)
        return IndexReport(
            reindexed=tuple(reindexed),
            removed=delta.removed,
            unreadable=tuple(unreadable),
            dangling_links=dangling,
        )

    def _sync_vectors(self, connection, present: dict[str, str], embedder: Embedder) -> None:
        vectors = VectorIndex(connection, embedder, self._config.index.vector_model)
        memory_paths = present
        known = vectors.known()
        for relative in sorted(set(known) - set(memory_paths)):
            vectors.remove_path(relative)
        for relative, digest in sorted(memory_paths.items()):
            if known.get(relative) == (digest, self._config.index.vector_model):
                continue
            record = self._load(self._layout.root / relative)
            if record is not None:
                vectors.upsert(relative, digest, record, chunking.chunks(record, self._config))
            else:
                vectors.remove_path(relative)

    def rebuild(self) -> IndexReport:
        return self.sync(rebuild=True)

    @contextmanager
    def search_connection(
        self, *, log: bool
    ) -> Iterator[tuple[sqlite3.Connection, Tokenizer]]:
        """Read the signature, FTS results and telemetry from one consistent projection."""
        tokenizer = self._tokenizer()
        transaction = "BEGIN IMMEDIATE" if log else "BEGIN"
        with self._database.connect() as connection:
            connection.execute(transaction)
            metadata = IndexMeta(connection)
            vector_ready = (
                not self._config.index.vector_enabled
                or metadata.get(VECTOR_MODEL_KEY) == self._config.index.vector_model
            )
            if (
                vector_ready
                and metadata.tokenizer() == tokenizer.signature
                and metadata.get(CHUNKING_KEY) == self._chunk_signature()
            ):
                yield connection, tokenizer
                return

        # Release the SQLite snapshot before waiting for the writer's filesystem lock.
        # This also restores a missing index from Markdown on the very first query.
        with store_lock(self._layout):
            self.sync()
            with self._database.connect() as connection:
                connection.execute(transaction)
                yield connection, tokenizer

    def search_tokenizer(self) -> Tokenizer:
        with self.search_connection(log=False) as (_, tokenizer):
            return tokenizer

    def _tokenizer(self) -> Tokenizer:
        index = self._config.index
        return Tokenizer(
            index.tokenizer, use_jieba=index.use_jieba, strict_jieba=index.strict_jieba
        )

    def _chunk_signature(self) -> str:
        return f"{self._config.index.chunk_min_chars}:{self._config.storage.slug_max_length}"

    def _present_hashes(self) -> dict[str, str]:
        present: dict[str, str] = {}
        for path in self._layout.truth_files():
            relative = path.relative_to(self._layout.root).as_posix()
            present[relative] = content_hash(
                path.read_text(encoding="utf-8"), self._config.index.hash_prefix_length
            )
        return present

    def _load(self, path: pathlib.Path) -> MemoryRecord | None:
        type_name = self._layout.type_of(path)
        if type_name is None:
            return None
        try:
            record = MemoryRecord.from_file(path)
            record_module.validate(record, self._config, self._schemas.get(type_name))
        except ValidationError:
            return None
        if record.type != type_name:
            return None
        return record

    def _dangling_links(self, index: SearchIndex) -> tuple[tuple[str, str], ...]:
        rows = index.rows()
        known = {row["name"] for row in rows}
        dangling: list[tuple[str, str]] = []
        for row in rows:
            for link in str(row["links"]).split(","):
                target = link.strip()
                if target and target not in known:
                    dangling.append((str(row["name"]), target))
        return tuple(sorted(dangling))
