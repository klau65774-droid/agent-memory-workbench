import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { build } from 'vite';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const backend = process.env.AGENT_MEMORY_BACKEND ?? join(frontend, '..', '后端');
const python = process.env.AGENT_MEMORY_PYTHON ?? join(backend, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
async function loadBrowserModule(entry) {
  const modules = await build({
    root: frontend, configFile: false, logLevel: 'silent',
    build: { lib: { entry: join(frontend, entry), formats: ['es'] }, write: false, minify: false },
  });
  const [chunk] = modules[0].output;
  return import('data:text/javascript;base64,' + Buffer.from(chunk.code).toString('base64'));
}
const client = await loadBrowserModule('src/api/client.ts');

const health = { status: 'ok', version: 'fixture', namespaces: ['default', 'finance-agent', 'secops-agent'] };
const response = (payload, id, ms) => Response.json(payload, {
  headers: { 'X-Correlation-ID': id, 'X-Memory-Latency-Ms': ms },
});

test('namespace generations reject late results and protect telemetry, including A → B → A', async (t) => {
  const pending = [];
  t.mock.method(globalThis, 'fetch', (path, init) =>
    new Promise((resolve) => pending.push({ path, init, resolve })),
  );
  const old = client.getHealth();
  client.setNamespace('finance-agent');
  const finance = client.getHealth();
  assert.equal(pending[1].init.headers.get('X-Namespace'), 'finance-agent');
  pending[1].resolve(response(health, 'finance', '2.5'));
  await finance;
  client.setNamespace('default');
  assert.deepEqual(client.getTrackingSnapshot(), { namespace: 'default', correlationId: null, latencyMs: null });
  pending[0].resolve(response(health, 'stale', '99'));
  await assert.rejects(old, { name: 'AbortError' });
  const previous = client.getHealth();
  const current = client.getHealth();
  pending[3].resolve(response(health, 'new', '3.125'));
  await current;
  pending[2].resolve(response(health, 'old', '8'));
  await previous;
  assert.deepEqual(client.getTrackingSnapshot(), { namespace: 'default', correlationId: 'new', latencyMs: 3.125 });
});

test('each correlation ID uses only its own latency header', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch');
  for (const [id, value] of [['missing', null], ['invalid', 'unknown']]) {
    fetch.mock.mockImplementation(async () => response(health, 'valid', '2.75'));
    await client.getHealth();
    assert.equal(client.getTrackingSnapshot().latencyMs, 2.75);
    const headers = new Headers({ 'X-Correlation-ID': id });
    if (value !== null) headers.set('X-Memory-Latency-Ms', value);
    fetch.mock.mockImplementation(async () => Response.json(health, { headers }));
    await client.getHealth();
    assert.deepEqual(client.getTrackingSnapshot(), { namespace: 'default', correlationId: id, latencyMs: null });
  }
});

test('malformed success JSON is rejected before React renders invalid nested types', async (t) => {
  const valid = {
    namespace: 'default', duration_ms: 1, actions: [{ kind: 'merge', target: 'a', detail: '' }],
    proposals: [], active_count_before: 2, active_count_after: 1,
  };
  const fetch = t.mock.method(globalThis, 'fetch', async () => response(valid, 'ok', '1'));
  assert.deepEqual(await client.runSleep({ dry_run: true }), valid);
  const [path, init] = fetch.mock.calls[0].arguments;
  assert.equal(path, '/v1/memory/manage/sleep');
  assert.equal(init.headers.get('X-Namespace'), 'default');
  assert.equal(init.headers.get('Content-Type'), 'application/json');
  assert.deepEqual(JSON.parse(init.body), { dry_run: true });
  fetch.mock.mockImplementation(async () => response({ ...valid, actions: ['merge'] }, 'bad', '2'));
  await assert.rejects(client.runSleep(), /响应字段与接口契约不符/);
  fetch.mock.mockImplementation(async () => new Response('bad request', { status: 422 }));
  await assert.rejects(client.listRecords(), /HTTP 422/);
});

function typeFromSchema(schema, dependencies) {
  if (schema.$ref) {
    const name = schema.$ref.split('/').at(-1);
    dependencies.add(name);
    return 'Server' + name;
  }
  if ('const' in schema) return JSON.stringify(schema.const);
  if (schema.enum) return schema.enum.map((value) => JSON.stringify(value)).join(' | ');
  if (schema.anyOf) return schema.anyOf.map((part) => typeFromSchema(part, dependencies)).join(' | ');
  switch (schema.type) {
    case 'array': return 'Array<' + typeFromSchema(schema.items, dependencies) + '>';
    case 'integer':
    case 'number': return 'number';
    case 'string':
    case 'boolean':
    case 'null': return schema.type;
    case 'object': {
      const fields = Object.entries(schema.properties).map(([key, field]) =>
        JSON.stringify(key) + (schema.required?.includes(key) ? ': ' : '?: ') + typeFromSchema(field, dependencies),
      );
      return '{' + fields.join('; ') + '}';
    }
    default: throw new Error('Unexpected OpenAPI schema: ' + JSON.stringify(schema));
  }
}

test('public TypeScript wire models equal the companion FastAPI OpenAPI schemas', {
  skip: !existsSync(python) && 'set AGENT_MEMORY_PYTHON to the installed backend interpreter',
}, () => {
  const run = spawnSync(python, ['-c',
    'import json; from agent_memory.server.main import app; print(json.dumps(app.openapi()["components"]["schemas"]))',
  ], { cwd: backend, encoding: 'utf8', timeout: 30000 });
  assert.equal(run.status, 0, run.stderr || run.error?.message);
  const schemas = JSON.parse(run.stdout);
  const names = {
    HealthResponse: 'HealthResponse', RecordView: 'MemoryRecordSummary', Edge: 'MemoryEdge',
    RecordsResponse: 'RecordListResponse', RecordDetail: 'MemoryRecordDetail', WriteRequest: 'WriteRecordRequest',
    WriteResponse: 'WriteRecordResponse', RecallRequest: 'RecallRequest', HitView: 'RecallHit',
    TokenBudgetView: 'TokenBudget', RecallResponse: 'RecallResponse', SleepRequest: 'SleepRequest',
    ActionView: 'SleepAction', ProposalView: 'SleepProposal', SleepResponse: 'SleepResponse',
    BenchmarkCase: 'BenchmarkCase', BenchmarkResponse: 'BenchmarkResponse',
  };
  const dependencies = new Set(Object.keys(names));
  const declarations = [];
  for (const name of dependencies) {
    declarations.push('type Server' + name + ' = ' + typeFromSchema(schemas[name], dependencies) + ';');
  }
  const source = [
    "import type * as Wire from './src/types/memory';",
    'type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends (<T>() => T extends B ? 1 : 2) ? true : false;',
    'type Assert<T extends true> = T;',
    ...declarations,
    ...Object.entries(names).map(([server, browser]) =>
      'type Verify' + server + ' = Assert<Equal<Server' + server + ', Wire.' + browser + '>>;',
    ),
  ].join('\n');
  const virtualFile = join(frontend, '__contract_test__.ts');
  const options = {
    strict: true, skipLibCheck: true, noEmit: true, target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
  };
  const host = ts.createCompilerHost(options);
  const original = host.getSourceFile.bind(host);
  host.getSourceFile = (file, ...args) => file === virtualFile || file === virtualFile.replaceAll('\\', '/')
    ? ts.createSourceFile(file, source, options.target)
    : original(file, ...args);
  const checks = ts.getPreEmitDiagnostics(ts.createProgram([virtualFile], options, host));
  assert.equal(checks.length, 0, ts.formatDiagnosticsWithColorAndContext(checks, {
    getCanonicalFileName: (name) => name, getCurrentDirectory: () => frontend, getNewLine: () => '\n',
  }));
});

test('the UI presets exercise their stated behavior on a fresh FastAPI store', {
  skip: !existsSync(python) && 'set AGENT_MEMORY_PYTHON to the installed backend interpreter',
}, async () => {
  const { RECALL_PRESETS, WRITE_PRESETS } = await loadBrowserModule('src/presets/samples.ts');
  const script = `
import json, sys, tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from agent_memory.server.main import create_app

recall, write = json.load(sys.stdin)
with tempfile.TemporaryDirectory() as root, TestClient(create_app(Path(root))) as client:
    for ns in client.get("/health").json()["namespaces"]:
        for preset in recall:
            res = client.post("/v1/memory/recall", headers={"X-Namespace": ns}, json=preset["request"])
            res.raise_for_status()
            result = res.json()
            hits = [h["name"] for h in result["hits"]]
            if preset["id"] == "recall-cjk-refund":
                assert "refund-sla-policy" in hits[:3], (ns, preset["id"], hits)
            elif preset["id"] == "recall-time-travel-2025":
                assert "legacy-refund-2025" in hits and "refund-sla-policy" not in hits
            elif preset["id"] == "recall-bilingual-vip":
                assert hits[:1] == ["vip-tier-policy"], (ns, preset["id"], hits)
            elif preset["id"] == "recall-poison-test":
                assert result["quarantined_filtered"] > 0 and "poisoned-ops-note" not in hits
            else:
                raise AssertionError("Missing expected result for " + preset["id"])
    for preset in write:
        res = client.post("/v1/memory/record", json=preset["request"])
        res.raise_for_status()
        result = res.json()
        blocked = preset["id"] in {"write-poison-en", "write-poison-cn"}
        assert result["written"] != blocked and result["quarantined"] == blocked, preset["id"]
        if preset["id"] == "write-pii-secret-redact":
            assert len(result["redactions_applied"]) >= 3, preset["id"]
        if preset["id"] == "write-order-id-benign":
            assert result["redactions_applied"] == [], preset["id"]
`;
  const run = spawnSync(python, ['-X', 'utf8', '-c', script], {
    cwd: backend, encoding: 'utf8', timeout: 60000, input: JSON.stringify([RECALL_PRESETS, WRITE_PRESETS]),
  });
  assert.equal(run.status, 0, run.stderr || run.error?.message);
});
