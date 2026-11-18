// The same validators describe the TypeScript responses and check JSON at the HTTP boundary.
export type Validator<T> = (value: unknown) => value is T;
type ValueOf<Check> = Check extends Validator<infer T> ? T : never;

const text: Validator<string> = (value): value is string => typeof value === 'string';
const finite: Validator<number> = (value): value is number =>
  typeof value === 'number' && Number.isFinite(value);
const integer: Validator<number> = (value): value is number => Number.isInteger(value);
const flag: Validator<boolean> = (value): value is boolean => typeof value === 'boolean';

function choice<T extends string>(...options: T[]): Validator<T> {
  return (value): value is T => options.some((option) => option === value);
}

function nullable<T>(check: Validator<T>): Validator<T | null> {
  return (value): value is T | null => value === null || check(value);
}

function list<T>(check: Validator<T>): Validator<T[]> {
  return (value): value is T[] => Array.isArray(value) && value.every(check);
}

function object<S extends Record<string, Validator<unknown>>>(fields: S) {
  type Model = { [K in keyof S]: ValueOf<S[K]> };
  return (value: unknown): value is Model => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
    const data = value as Record<string, unknown>;
    return Object.entries(fields).every(([key, check]) =>
      Object.prototype.hasOwnProperty.call(data, key) && check(data[key]),
    );
  };
}

const namespace = choice('default', 'finance-agent', 'secops-agent');
const level = choice('abstract', 'outline', 'full');
const status = choice('active', 'invalid', 'quarantined');
const trust = choice('trusted', 'suspicious', 'quarantined');
const edgeKind = choice('link', 'supersedes');
const plane = choice('retrieval', 'safety', 'context');
const strings = list(text);

export type Namespace = ValueOf<typeof namespace>;
export type MemoryLevel = ValueOf<typeof level>;
export type MemoryKind = 'decision' | 'experience' | 'fact';
export type MemoryEdgeKind = ValueOf<typeof edgeKind>;
export type BenchmarkPlane = ValueOf<typeof plane>;

export interface TrackingSnapshot {
  namespace: Namespace;
  correlationId: string | null;
  latencyMs: number | null;
}

export const healthResponse = object({ status: choice('ok'), version: text, namespaces: list(namespace) });
export type HealthResponse = ValueOf<typeof healthResponse>;

const record = object({
  name: text, path: text, abstract: text, type: text, author: text, created: text, updated: text,
  valid_from: text, invalid_at: nullable(text), superseded_by: nullable(text), weight: finite,
  status, links: strings, provenance: strings, trust_level: trust, poison_reasons: strings,
  redacted_count: integer, access_count: integer,
});
export type MemoryRecordSummary = ValueOf<typeof record>;

const edge = object({ source: text, target: text, kind: edgeKind });
export type MemoryEdge = ValueOf<typeof edge>;

export const recordsResponse = object({
  namespace, total_active: integer, total_history: integer, total_quarantined: integer,
  records: list(record), edges: list(edge),
});
export type RecordListResponse = ValueOf<typeof recordsResponse>;

export const detailResponse = object({
  name: text, level, text, abstract_tokens: integer, outline_tokens: integer, full_tokens: integer,
  token_savings_pct: finite, trust_level: trust, poison_reasons: strings, record,
});
export type MemoryRecordDetail = ValueOf<typeof detailResponse>;

export interface RecallRequest {
  query: string;
  scope?: string | null;
  as_of?: string | null;
  limit?: number;
  include_quarantined?: boolean;
  spotlighting?: boolean;
}

const budget = object({
  full_store_tokens: integer, progressive_context_tokens: integer, tokens_saved: integer,
  savings_pct: finite,
});
export type TokenBudget = ValueOf<typeof budget>;

const hit = object({
  name: text, path: text, abstract: text, anchor: text, heading: text, type: text, updated: text,
  status, weight: finite, relevance: finite, recency: finite, access_boost: finite, score: finite,
  trust_level: trust, poison_reasons: strings, cited_by: strings,
});
export type RecallHit = ValueOf<typeof hit>;

export const recallResponse = object({
  query: text, tokenizer_used: text, query_tokens: strings, as_of: nullable(text),
  quarantined_filtered: integer, hits: list(hit), context_preview: text, token_budget: budget,
});
export type RecallResponse = ValueOf<typeof recallResponse>;

export interface WriteRecordRequest {
  name: string;
  abstract: string;
  type: string;
  body?: string;
  author?: string;
  weight?: number;
  links?: string[];
  valid_from?: string | null;
  supersedes?: string | null;
  quarantine_on_poison?: boolean;
}

export const writeResponse = object({
  written: flag, quarantined: flag, trust_level: trust, poison_reasons: strings,
  redactions_applied: strings, redacted_body_preview: text, record,
});
export type WriteRecordResponse = ValueOf<typeof writeResponse>;

export interface SleepRequest {
  dry_run?: boolean;
}

const proposal = object({ id: text, kind: text, targets: strings, reason: text, evidence: text });
export type SleepProposal = ValueOf<typeof proposal>;

const action = object({ kind: text, target: text, detail: text });
export type SleepAction = ValueOf<typeof action>;

export const sleepResponse = object({
  namespace, duration_ms: finite, actions: list(action), proposals: list(proposal),
  active_count_before: integer, active_count_after: integer,
});
export type SleepResponse = ValueOf<typeof sleepResponse>;

const benchmarkCase = object({
  case_id: text, category: text, plane, description: text, expected: text, actual: text,
  passed: flag, latency_ms: finite,
});
export type BenchmarkCase = ValueOf<typeof benchmarkCase>;

export const benchmarkResponse = object({
  total_cases: integer, cjk_recall_at_3_legacy_pct: finite, cjk_recall_at_3_enhanced_pct: finite,
  poison_defense_rate_pct: finite, benign_false_positive_rate_pct: finite,
  redaction_coverage_pct: finite, avg_token_savings_pct: finite, avg_latency_ms: finite,
  p95_latency_ms: finite, cases: list(benchmarkCase),
});
export type BenchmarkResponse = ValueOf<typeof benchmarkResponse>;

export interface Preset<T> {
  id: string;
  label: string;
  request: T;
}

export type RecallFormInput = Required<
  Pick<RecallRequest, 'query' | 'as_of' | 'include_quarantined' | 'spotlighting'>
>;
export type WriteFormInput = Required<
  Pick<WriteRecordRequest, 'name' | 'abstract' | 'body' | 'links' | 'quarantine_on_poison'>
> & { type: MemoryKind };
export type RecallPreset = Preset<RecallFormInput>;
export type WritePreset = Preset<WriteFormInput>;
