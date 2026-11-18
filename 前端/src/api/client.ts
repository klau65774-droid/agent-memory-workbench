import type {
  BenchmarkResponse,
  HealthResponse,
  MemoryLevel,
  MemoryRecordDetail,
  Namespace,
  RecallRequest,
  RecallResponse,
  RecordListResponse,
  SleepResponse,
  SleepRequest,
  TrackingSnapshot,
  Validator,
  WriteRecordRequest,
  WriteRecordResponse,
} from '../types/memory';
import {
  benchmarkResponse, detailResponse, healthResponse, recallResponse, recordsResponse,
  sleepResponse, writeResponse,
} from '../types/memory.ts';

type TrackingListener = (snapshot: TrackingSnapshot) => void;

const listeners = new Set<TrackingListener>();
let currentNamespace: Namespace = 'default';
let namespaceGeneration = 0;
let nextRequestId = 0;
let lastTrackedId = 0;
let snapshot: TrackingSnapshot = {
  namespace: currentNamespace,
  correlationId: null,
  latencyMs: null,
};

function publish(next: TrackingSnapshot): void {
  snapshot = next;
  listeners.forEach((listener) => listener(snapshot));
}

export function setNamespace(namespace: Namespace): void {
  if (namespace === currentNamespace) return;
  currentNamespace = namespace;
  namespaceGeneration++;
  publish({ namespace, correlationId: null, latencyMs: null });
}

export function getTrackingSnapshot(): TrackingSnapshot {
  return snapshot;
}

export function subscribeTracking(listener: TrackingListener): () => void {
  listeners.add(listener);
  listener(snapshot);
  return () => {
    listeners.delete(listener);
  };
}

export function parseLatencyHeader(value: string | null): number | null {
  if (value === null) {
    return null;
  }

  const trimmed = value.trim();
  if (trimmed.includes('=') && !trimmed.startsWith('engine=')) {
    return null;
  }

  const candidate = trimmed.startsWith('engine=') ? trimmed.slice(7).trim() : trimmed;
  if (!/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(candidate)) {
    return null;
  }

  const parsed = Number(candidate);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function trackResponse(response: Response): void {
  publish({
    namespace: currentNamespace,
    correlationId: response.headers.get('X-Correlation-ID'),
    latencyMs: parseLatencyHeader(response.headers.get('X-Memory-Latency-Ms')),
  });
}

async function request<T>(path: string, validate: Validator<T>, init: RequestInit = {}): Promise<T> {
  const generation = namespaceGeneration;
  const requestId = ++nextRequestId;
  const headers = new Headers(init.headers);
  headers.set('X-Namespace', currentNamespace);
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(path, { ...init, headers });
  if (generation === namespaceGeneration && requestId > lastTrackedId) {
    lastTrackedId = requestId;
    trackResponse(response);
  }

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
  }

  const payload: unknown = await response.json();
  if (generation !== namespaceGeneration) {
    throw new DOMException('命名空间已切换', 'AbortError');
  }
  if (!validate(payload)) {
    throw new Error(`响应字段与接口契约不符（${path}）`);
  }
  return payload;
}

export function getHealth(): Promise<HealthResponse> {
  return request('/health', healthResponse);
}

export function listRecords(): Promise<RecordListResponse> {
  return request(
    '/v1/memory/records?include_invalid=true&include_quarantined=true',
    recordsResponse,
  );
}

export function getRecord(name: string, level: MemoryLevel): Promise<MemoryRecordDetail> {
  return request(
    `/v1/memory/records/${encodeURIComponent(name)}?level=${level}`,
    detailResponse,
  );
}

export function writeRecord(input: WriteRecordRequest): Promise<WriteRecordResponse> {
  return request('/v1/memory/record', writeResponse, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function recallMemories(input: RecallRequest): Promise<RecallResponse> {
  return request('/v1/memory/recall', recallResponse, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function runSleep(input: SleepRequest = {}): Promise<SleepResponse> {
  return request('/v1/memory/manage/sleep', sleepResponse, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function runBenchmark(): Promise<BenchmarkResponse> {
  return request('/v1/memory/evals/benchmark', benchmarkResponse, {
    method: 'POST',
  });
}
