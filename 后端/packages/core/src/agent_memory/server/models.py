"""JSON contracts for the memory console."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, FiniteFloat

from ..core import timestamp
from ..core.poison_guard import TrustLevel
from .demo import Namespace

type RecordStatus = Literal["active", "invalid", "quarantined"]
_DEFAULT_RECALL_LIMIT = 5


def _validate_instant(value: str) -> str:
    if not timestamp.is_valid(value):
        raise ValueError("must be an ISO 8601 day or zone-aware instant")
    return value


type Instant = Annotated[str, AfterValidator(_validate_instant)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    namespaces: list[Namespace]


class RecordView(BaseModel):
    name: str
    path: str
    abstract: str
    type: str
    author: str
    created: str
    updated: str
    valid_from: str
    invalid_at: str | None
    superseded_by: str | None
    weight: float
    status: RecordStatus
    links: list[str]
    provenance: list[str]
    trust_level: TrustLevel
    poison_reasons: list[str]
    redacted_count: int
    access_count: int


class Edge(BaseModel):
    source: str
    target: str
    kind: Literal["link", "supersedes"]


class RecordsResponse(BaseModel):
    namespace: Namespace
    total_active: int
    total_history: int
    total_quarantined: int
    records: list[RecordView]
    edges: list[Edge]


class RecordDetail(BaseModel):
    name: str
    level: Literal["abstract", "outline", "full"]
    text: str
    abstract_tokens: int
    outline_tokens: int
    full_tokens: int
    token_savings_pct: float
    trust_level: TrustLevel
    poison_reasons: list[str]
    record: RecordView


class WriteRequest(RequestModel):
    name: str = Field(min_length=1)
    abstract: str = Field(min_length=1)
    type: str
    body: str = ""
    author: str = "console-user"
    weight: FiniteFloat = 1.0
    links: list[str] = Field(default_factory=list)
    valid_from: Instant | None = None
    supersedes: str | None = None
    quarantine_on_poison: bool = True


class WriteResponse(BaseModel):
    written: bool
    quarantined: bool
    trust_level: TrustLevel
    poison_reasons: list[str]
    redactions_applied: list[str]
    redacted_body_preview: str
    record: RecordView


class RecallRequest(RequestModel):
    query: str
    scope: str | None = None
    as_of: Instant | None = None
    limit: int = Field(default=_DEFAULT_RECALL_LIMIT, ge=1)
    include_quarantined: bool = False
    spotlighting: bool = True


class HitView(BaseModel):
    name: str
    path: str
    abstract: str
    anchor: str
    heading: str
    type: str
    updated: str
    status: RecordStatus
    weight: float
    relevance: float
    recency: float
    access_boost: float
    score: float
    trust_level: TrustLevel
    poison_reasons: list[str]
    cited_by: list[str]


class TokenBudgetView(BaseModel):
    full_store_tokens: int
    progressive_context_tokens: int
    tokens_saved: int
    savings_pct: float


class RecallResponse(BaseModel):
    query: str
    tokenizer_used: str
    query_tokens: list[str]
    as_of: str | None
    quarantined_filtered: int
    hits: list[HitView]
    context_preview: str
    token_budget: TokenBudgetView


class SleepRequest(RequestModel):
    dry_run: bool = False


class ActionView(BaseModel):
    kind: str
    target: str
    detail: str


class ProposalView(BaseModel):
    id: str
    kind: str
    targets: list[str]
    reason: str
    evidence: str


class SleepResponse(BaseModel):
    namespace: Namespace
    duration_ms: float
    actions: list[ActionView]
    proposals: list[ProposalView]
    active_count_before: int
    active_count_after: int


class BenchmarkCase(BaseModel):
    case_id: str
    category: str
    plane: Literal["retrieval", "safety", "context"]
    description: str
    expected: str
    actual: str
    passed: bool
    latency_ms: float


class BenchmarkResponse(BaseModel):
    total_cases: int
    cjk_recall_at_3_legacy_pct: float
    cjk_recall_at_3_enhanced_pct: float
    poison_defense_rate_pct: float
    benign_false_positive_rate_pct: float
    redaction_coverage_pct: float
    avg_token_savings_pct: float
    avg_latency_ms: float
    p95_latency_ms: float
    cases: list[BenchmarkCase]
