"""Memory operations shared by the HTTP routes and their offline evaluation."""

from __future__ import annotations

import dataclasses
import time
from collections import defaultdict

from ..core.access_log import AccessLog
from ..core.context import (
    DisclosureLevel,
    build_progressive,
    disclose,
    estimate_tokens,
    savings_pct,
)
from ..core.database import Database
from ..core.errors import FieldError, ValidationError
from ..core.manage import Manage
from ..core.placement import WINDOWS_RESERVED_STEMS
from ..core.poison_guard import SafetyReport, inspect_memory_safety, wrap_with_spotlighting
from ..core.recall import Recall
from ..core.record import MemoryRecord
from ..core.redaction import redact_with_report
from ..core.slug import is_valid_slug
from ..core.store import Store
from . import LATENCY_DECIMAL_PLACES, MILLISECONDS_PER_SECOND
from .demo import Namespace
from .demo import locked_store as locked_store
from .models import (
    ActionView,
    Edge,
    HitView,
    ProposalView,
    RecallRequest,
    RecallResponse,
    RecordDetail,
    RecordsResponse,
    RecordStatus,
    RecordView,
    SleepResponse,
    TokenBudgetView,
    WriteRequest,
    WriteResponse,
)

_REDACTED_COUNT = "console_redacted_count"
_PREVIEW_CHARS = 512


def _access_counts(store: Store) -> dict[str, int]:
    with Database(store.layout).connect() as connection:
        return AccessLog(connection).counts()


def _safety(record: MemoryRecord) -> SafetyReport:
    return inspect_memory_safety(record.abstract, record.body)


def _status(record: MemoryRecord, safety: SafetyReport) -> RecordStatus:
    if safety.trust_level == "quarantined":
        return "quarantined"
    return "active" if record.is_active() else "invalid"


def _record_view(
    store: Store, record: MemoryRecord, counts: dict[str, int], safety: SafetyReport
) -> RecordView:
    assert record.path is not None
    raw_count = record.fields.get(_REDACTED_COUNT, "0")
    try:
        redacted_count = max(0, int(raw_count))
    except ValueError:
        redacted_count = 0
    return RecordView(
        name=record.name,
        path=record.path.relative_to(store.root).as_posix(),
        abstract=record.abstract,
        type=record.type,
        author=record.author,
        created=record.created,
        updated=record.updated,
        valid_from=record.valid_from or record.created,
        invalid_at=record.invalid_at,
        superseded_by=record.superseded_by,
        weight=record.weight,
        status=_status(record, safety),
        links=record.links,
        provenance=record.provenance,
        trust_level=safety.trust_level,
        poison_reasons=safety.reasons,
        redacted_count=redacted_count,
        access_count=counts.get(record.name, 0),
    )


def list_records(
    store: Store, namespace: Namespace, include_invalid: bool, include_quarantined: bool
) -> RecordsResponse:
    counts = _access_counts(store)
    records = [
        _record_view(store, record, counts, _safety(record))
        for record in store.records(include_invalid=True)
    ]
    visible = {
        record.name: record
        for record in records
        if (include_invalid or record.invalid_at is None)
        and (include_quarantined or record.status != "quarantined")
    }
    edges = [
        Edge(source=record.name, target=target, kind="link")
        for record in visible.values()
        for target in record.links
        if target in visible
    ]
    edges.extend(
        Edge(source=record.superseded_by, target=record.name, kind="supersedes")
        for record in visible.values()
        if record.superseded_by is not None and record.superseded_by in visible
    )
    return RecordsResponse(
        namespace=namespace,
        total_active=sum(record.status == "active" for record in records),
        total_history=sum(record.status == "invalid" for record in records),
        total_quarantined=sum(record.status == "quarantined" for record in records),
        records=list(visible.values()),
        edges=edges,
    )


def read_record(store: Store, name: str, level: DisclosureLevel) -> RecordDetail:
    record = store.read(name, level=level).record
    safety = _safety(record)
    disclosure = disclose(record, store.config)
    abstract_tokens = estimate_tokens(disclosure.abstract)
    outline_tokens = estimate_tokens(disclosure.outline)
    full_tokens = estimate_tokens(disclosure.full)
    text = disclosure.at(level)
    return RecordDetail(
        name=record.name,
        level=level,
        text=text,
        abstract_tokens=abstract_tokens,
        outline_tokens=outline_tokens,
        full_tokens=full_tokens,
        token_savings_pct=savings_pct(full_tokens, estimate_tokens(text)),
        trust_level=safety.trust_level,
        poison_reasons=safety.reasons,
        record=_record_view(store, record, _access_counts(store), safety),
    )


def write_record(store: Store, request: WriteRequest) -> WriteResponse:
    # These strings are stable file and graph identifiers. Redacting them in place
    # could silently redirect a write or an edge to another record.
    for field, values in (
        ("name", [request.name]), ("links", request.links),
        ("supersedes", [request.supersedes] if request.supersedes is not None else []),
    ):
        for value in values:
            if (not is_valid_slug(value) or value in WINDOWS_RESERVED_STEMS
                    or len(value) > store.config.storage.slug_max_length):
                raise ValidationError([FieldError(field, "expected a bounded kebab-case slug")])
            if redact_with_report(value)[1]:
                raise ValidationError([FieldError(field, "use a non-sensitive identifier")])
    abstract, abstract_report = redact_with_report(request.abstract)
    body, body_report = redact_with_report(request.body)
    author, author_report = redact_with_report(request.author)
    redactions = abstract_report + body_report + author_report
    spec = request.model_dump(exclude={"quarantine_on_poison"})
    spec.update(
        abstract=abstract,
        body=body,
        author=author,
        fields={_REDACTED_COUNT: str(len(redactions))},
    )

    def accept(record: MemoryRecord) -> bool:
        if _safety(record).trust_level == "quarantined":
            return (not request.quarantine_on_poison and request.supersedes is None
                    and store.find(record.name) is None)
        if request.supersedes is not None and store.find(record.name) is not None:
            raise ValidationError([FieldError("name", "a successor needs an unused identifier")])
        return True

    candidate, written = store.record_if(accept, **spec)
    safety = _safety(candidate)
    return WriteResponse(
        written=written,
        quarantined=safety.trust_level == "quarantined",
        trust_level=safety.trust_level,
        poison_reasons=safety.reasons,
        redactions_applied=list(dict.fromkeys(redactions)),
        redacted_body_preview=body[:_PREVIEW_CHARS],
        record=_record_view(store, candidate, _access_counts(store), safety),
    )


def recall_records(store: Store, request: RecallRequest, *, log: bool = True) -> RecallResponse:
    records = store.records(include_invalid=True)
    result = Recall(store).recall_enhanced(
        request.query,
        scope=request.scope,
        as_of=request.as_of,
        limit=request.limit,
        include_quarantined=request.include_quarantined,
        records=records,
        log=log,
    )
    preview, budget = build_progressive(
        store, result.hits, records, spotlighting=request.spotlighting
    )
    by_name = {record.name: record for record in records}
    cited_by: dict[str, list[str]] = defaultdict(list)
    for name in sorted(result.visible_names):
        for target in by_name[name].links:
            cited_by[target].append(name)

    hits = []
    for item in result.hits:
        hit, safety = item.hit, item.safety
        record = by_name[hit.name]
        assert record.path is not None
        abstract, heading = hit.abstract, hit.heading
        if request.spotlighting:
            abstract = wrap_with_spotlighting(abstract, safety.trust_level)
            if heading:
                heading = wrap_with_spotlighting(heading, safety.trust_level)
        hits.append(
            HitView(
                name=hit.name,
                path=record.path.relative_to(store.root).as_posix(),
                abstract=abstract,
                anchor=hit.anchor,
                heading=heading,
                type=hit.type,
                updated=hit.updated,
                status=_status(record, safety),
                weight=hit.weight,
                relevance=hit.relevance,
                recency=hit.recency,
                access_boost=item.access_boost,
                score=hit.score,
                trust_level=safety.trust_level,
                poison_reasons=safety.reasons,
                cited_by=cited_by[hit.name],
            )
        )
    return RecallResponse(
        query=request.query,
        tokenizer_used=store.config.index.tokenizer,
        query_tokens=result.query_tokens,
        as_of=request.as_of,
        quarantined_filtered=result.quarantined_filtered,
        hits=hits,
        context_preview=preview.text,
        token_budget=TokenBudgetView(**dataclasses.asdict(budget)),
    )


def sleep(store: Store, namespace: Namespace, *, dry_run: bool) -> SleepResponse:
    start = time.perf_counter()
    active = [record for record in store.records() if _not_quarantined(record)]
    manager = Manage(store, include_record=_not_quarantined)
    actions: list[ActionView] = []
    if dry_run:
        proposals = manager.proposals(records=active)
        after = len(active)
    else:
        report = manager.sleep()
        proposals = list(report.proposals)
        actions = [ActionView(**action.as_dict()) for action in report.actions]
        after = report.inspected
    return SleepResponse(
        namespace=namespace,
        duration_ms=round(
            (time.perf_counter() - start) * MILLISECONDS_PER_SECOND, LATENCY_DECIMAL_PLACES
        ),
        actions=actions,
        proposals=[
            ProposalView(
                id=proposal.id,
                kind=proposal.kind,
                targets=list(proposal.targets),
                reason=proposal.reason,
                evidence=proposal.evidence,
            )
            for proposal in proposals
        ],
        active_count_before=len(active),
        active_count_after=after,
    )


def _not_quarantined(record: MemoryRecord) -> bool:
    return _safety(record).trust_level != "quarantined"
