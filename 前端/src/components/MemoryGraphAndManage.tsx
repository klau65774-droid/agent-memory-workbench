import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { listRecords, runSleep, writeRecord } from '../api/client';
import { WRITE_PRESETS } from '../presets/samples';
import type {
  MemoryEdge,
  MemoryRecordSummary,
  SleepResponse,
  WritePreset,
  WriteFormInput,
  WriteRecordResponse,
} from '../types/memory';

const VIEW_BOX_WIDTH = 800;
const VIEW_BOX_HEIGHT = 500;
const GRAPH_CENTER_X = VIEW_BOX_WIDTH / 2;
const GRAPH_CENTER_Y = VIEW_BOX_HEIGHT / 2;
const NODE_RADIUS = 28;
const BASE_RADIUS = 190;

interface GraphNode {
  record: MemoryRecordSummary;
  x: number;
  y: number;
  index: number;
}

interface GraphEdge {
  edge: MemoryEdge;
  source: GraphNode;
  target: GraphNode;
}

function getNodeColor(record: MemoryRecordSummary): string {
  const status = record.status.toLowerCase();
  const trust = record.trust_level.toLowerCase();
  if (status.includes('quarantined') || trust.includes('quarantined')) return 'var(--red)';
  if (status.includes('invalid') || status.includes('history')) return 'var(--muted)';
  if (status.includes('active') && trust.includes('trusted')) return 'var(--accent)';
  return 'var(--cyan)';
}

function getTrustBadgeClass(trustLevel: string): string {
  const level = trustLevel.toLowerCase();
  if (level.includes('quarantined')) return 'badge-quarantined';
  if (level.includes('suspicious')) return 'badge-suspicious';
  if (level.includes('trusted')) return 'badge-trusted';
  return '';
}

function formatTimestamp(value: string | null): string {
  return value ?? '—';
}

function formatList(items: string[] | null | undefined, empty: string): string {
  if (!items || items.length === 0) return empty;
  return items.join(', ');
}

export default function MemoryGraphAndManage(): JSX.Element {
  const graphId = useId();
  const linkMarkerId = `link-arrow-${graphId}`;
  const supersedesMarkerId = `supersedes-arrow-${graphId}`;

  const [records, setRecords] = useState<MemoryRecordSummary[]>([]);
  const [edges, setEdges] = useState<MemoryEdge[]>([]);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [form, setForm] = useState<WriteFormInput & { linksInput: string }>({
    name: '',
    type: 'decision',
    abstract: '',
    body: '',
    links: [],
    linksInput: '',
    quarantine_on_poison: true,
  });
  const [writeLoading, setWriteLoading] = useState(false);
  const [writeError, setWriteError] = useState<string | null>(null);
  const [writeResult, setWriteResult] = useState<WriteRecordResponse | null>(null);
  const [submittedBody, setSubmittedBody] = useState<string>('');

  const [sleepLoading, setSleepLoading] = useState(false);
  const [sleepError, setSleepError] = useState<string | null>(null);
  const [sleepResult, setSleepResult] = useState<SleepResponse | null>(null);

  const listSeqRef = useRef(0);
  const writeSeqRef = useRef(0);
  const sleepSeqRef = useRef(0);

  const loadRecords = async (): Promise<void> => {
    const seq = ++listSeqRef.current;
    setListLoading(true);
    setListError(null);
    try {
      const data = await listRecords();
      if (seq !== listSeqRef.current) return;
      setRecords(data.records);
      setEdges(data.edges);
      setSelectedName((prev) => {
        const names = new Set(data.records.map((r) => r.name));
        if (prev !== null && names.has(prev)) return prev;
        return data.records[0]?.name ?? null;
      });
    } catch (err) {
      if (seq !== listSeqRef.current) return;
      setListError(err instanceof Error ? err.message : String(err));
    } finally {
      if (seq === listSeqRef.current) setListLoading(false);
    }
  };

  useEffect(() => {
    void loadRecords();
    return () => {
      ++listSeqRef.current;
      ++writeSeqRef.current;
      ++sleepSeqRef.current;
    };
  }, []);

  const graphData = useMemo<{ nodes: GraphNode[]; edges: GraphEdge[] }>(() => {
    const uniqueByName = new Map<string, MemoryRecordSummary>();
    for (const record of records) {
      if (!uniqueByName.has(record.name)) {
        uniqueByName.set(record.name, record);
      }
    }
    const nodeList = Array.from(uniqueByName.values());
    const count = nodeList.length;

    const nodes: GraphNode[] = nodeList.map((record, index) => {
      if (count <= 1) {
        return { record, x: GRAPH_CENTER_X, y: GRAPH_CENTER_Y, index };
      }
      const angle = -Math.PI / 2 + (2 * Math.PI * index) / count;
      const radius = Math.min(BASE_RADIUS, Math.max(80, count * 32));
      return {
        record,
        x: GRAPH_CENTER_X + radius * Math.cos(angle),
        y: GRAPH_CENTER_Y + radius * Math.sin(angle),
        index,
      };
    });

    const nodeByName = new Map(nodes.map((n) => [n.record.name, n]));
    const validEdges: GraphEdge[] = [];
    for (const edge of edges) {
      const source = nodeByName.get(edge.source);
      const target = nodeByName.get(edge.target);
      if (source !== undefined && target !== undefined) {
        validEdges.push({ edge, source, target });
      }
    }

    return { nodes, edges: validEdges };
  }, [records, edges]);

  const selectedRecord = useMemo<MemoryRecordSummary | null>(() => {
    return records.find((r) => r.name === selectedName) ?? records[0] ?? null;
  }, [records, selectedName]);

  const handleWrite = async (request: WriteFormInput): Promise<void> => {
    setSubmittedBody(request.body);
    const seq = ++writeSeqRef.current;
    setWriteLoading(true);
    setWriteError(null);
    setWriteResult(null);
    try {
      const result = await writeRecord(request);
      if (seq !== writeSeqRef.current) return;
      setWriteResult(result);
      if (result.written) void loadRecords();
    } catch (err) {
      if (seq !== writeSeqRef.current) return;
      setWriteError(err instanceof Error ? err.message : String(err));
      setWriteResult(null);
    } finally {
      if (seq === writeSeqRef.current) setWriteLoading(false);
    }
  };

  const applyPreset = (preset: WritePreset): void => {
    setForm({
      ...preset.request,
      linksInput: preset.request.links.join(', '),
    });
    handleWrite(preset.request);
  };

  const submitForm = (): void => {
    const request: WriteFormInput = {
      name: form.name.trim(),
      type: form.type,
      abstract: form.abstract.trim(),
      body: form.body,
      links: form.linksInput
        .split(',')
        .map((s) => s.trim())
        .filter((s) => s.length > 0),
      quarantine_on_poison: form.quarantine_on_poison,
    };
    handleWrite(request);
  };

  const handleSleep = async (): Promise<void> => {
    const seq = ++sleepSeqRef.current;
    setSleepLoading(true);
    setSleepError(null);
    setSleepResult(null);
    try {
      const result = await runSleep();
      if (seq !== sleepSeqRef.current) return;
      setSleepResult(result);
      void loadRecords();
    } catch (err) {
      if (seq !== sleepSeqRef.current) return;
      setSleepError(err instanceof Error ? err.message : String(err));
      setSleepResult(null);
    } finally {
      if (seq === sleepSeqRef.current) setSleepLoading(false);
    }
  };

  const renderEdgePath = (graphEdge: GraphEdge): string | null => {
    const { source, target } = graphEdge;
    if (source.index === target.index) {
      const x = source.x;
      const y = source.y;
      return `M ${x} ${y - NODE_RADIUS} C ${x - 50} ${y - 90}, ${x + 50} ${y - 90}, ${x} ${y - NODE_RADIUS}`;
    }
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const dist = Math.hypot(dx, dy) || 1;
    const sx = source.x + (dx / dist) * NODE_RADIUS;
    const sy = source.y + (dy / dist) * NODE_RADIUS;
    const tx = target.x - (dx / dist) * NODE_RADIUS;
    const ty = target.y - (dy / dist) * NODE_RADIUS;
    return `M ${sx} ${sy} L ${tx} ${ty}`;
  };

  return (
    <section className="memory-manage-page">
      <header className="page-intro">
        <div>
          <div className="eyebrow">Memory Graph &amp; Manage</div>
          <h1>记忆图谱与管理台</h1>
          <p>
            可视化记忆节点与关系、执行安全写入并观察脱敏/投毒判定，以及触发 Sleep-Time
            整理提案。
          </p>
        </div>
      </header>

      <div className="stack">
        <div className="manage-grid">
          <div className="stack">
            <section className="panel">
              <div className="panel-header">
                <div>
                  <h2 className="panel-title">记忆关系图谱</h2>
                </div>
                {listLoading && <span className="badge">加载中…</span>}
              </div>
              <div className="panel-body" style={{ minHeight: 360, position: 'relative' }}>
                {listError !== null && (
                  <div className="error-banner" style={{ marginBottom: 12 }}>
                    图谱加载失败：{listError}
                  </div>
                )}
                {graphData.nodes.length === 0 ? (
                  !listLoading && !listError && <div className="empty-state">暂无记忆节点</div>
                ) : (
                  <svg
                    viewBox={`0 0 ${VIEW_BOX_WIDTH} ${VIEW_BOX_HEIGHT}`}
                    width="100%"
                    height="100%"
                    role="img"
                    aria-label="记忆关系环形图谱"
                    preserveAspectRatio="xMidYMid meet"
                    style={{ display: 'block', height: 360, width: '100%' }}
                  >
                    <defs>
                      <marker
                        id={linkMarkerId}
                        markerWidth="10"
                        markerHeight="10"
                        refX="9"
                        refY="3"
                        orient="auto"
                        markerUnits="strokeWidth"
                      >
                        <path d="M0,0 L0,6 L9,3 z" fill="var(--muted)" />
                      </marker>
                      <marker
                        id={supersedesMarkerId}
                        markerWidth="10"
                        markerHeight="10"
                        refX="9"
                        refY="3"
                        orient="auto"
                        markerUnits="strokeWidth"
                      >
                        <path d="M0,0 L0,6 L9,3 z" fill="var(--cyan)" />
                      </marker>
                    </defs>

                    {graphData.edges.map((graphEdge, idx) => {
                      const d = renderEdgePath(graphEdge);
                      if (d === null) return null;
                      const isSupersedes = graphEdge.edge.kind === 'supersedes';
                      return (
                        <path
                          key={`edge-${graphEdge.edge.source}-${graphEdge.edge.target}-${graphEdge.edge.kind}-${idx}`}
                          d={d}
                          fill="none"
                          stroke={isSupersedes ? 'var(--cyan)' : 'var(--muted)'}
                          strokeWidth={isSupersedes ? 1.5 : 1}
                          strokeDasharray={isSupersedes ? '6,4' : undefined}
                          markerEnd={`url(#${isSupersedes ? supersedesMarkerId : linkMarkerId})`}
                          opacity={0.85}
                        />
                      );
                    })}

                    {graphData.nodes.map((node) => {
                      const isSelected = node.record.name === selectedRecord?.name;
                      const label =
                        node.record.name.length > 10
                          ? `${node.record.name.slice(0, 9)}…`
                          : node.record.name;
                      return (
                        <g
                          key={node.record.name}
                          role="button"
                          tabIndex={0}
                          aria-label={`节点 ${node.record.name}，状态 ${node.record.status}，信任 ${node.record.trust_level}`}
                          transform={`translate(${node.x}, ${node.y})`}
                          onClick={() => setSelectedName(node.record.name)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              setSelectedName(node.record.name);
                            }
                          }}
                          style={{ cursor: 'pointer' }}
                        >
                          {isSelected && (
                            <circle r={NODE_RADIUS + 5} fill="none" stroke="var(--accent)" strokeWidth={2} />
                          )}
                          <circle
                            r={NODE_RADIUS}
                            fill={getNodeColor(node.record)}
                            stroke="rgba(7, 11, 18, 0.7)"
                            strokeWidth={2}
                          />
                          <text
                            textAnchor="middle"
                            dominantBaseline="middle"
                            fill="#070b12"
                            fontSize={9}
                            fontWeight={600}
                            style={{ pointerEvents: 'none' }}
                          >
                            {label}
                          </text>
                          <title>{node.record.name}</title>
                        </g>
                      );
                    })}
                  </svg>
                )}
              </div>
            </section>

            <section className="panel">
              <div className="panel-header">
                <h2 className="panel-title">Frontmatter 元数据</h2>
              </div>
              <div className="panel-body">
                {selectedRecord === null ? (
                  <div className="empty-state">未选择节点</div>
                ) : (
                  <div className="form-stack">
                    <div className="form-grid">
                      <div className="field">
                        <span className="field-label">Name</span>
                        <div className="mono">{selectedRecord.name}</div>
                      </div>
                      <div className="field">
                        <span className="field-label">Type</span>
                        <div className="mono">{selectedRecord.type}</div>
                      </div>
                      <div className="field">
                        <span className="field-label">Status</span>
                        <div>
                          <span className="badge">{selectedRecord.status}</span>
                        </div>
                      </div>
                      <div className="field">
                        <span className="field-label">Trust Level</span>
                        <div>
                          <span className={`badge ${getTrustBadgeClass(selectedRecord.trust_level)}`}>
                            {selectedRecord.trust_level}
                          </span>
                        </div>
                      </div>
                      <div className="field">
                        <span className="field-label">Valid From</span>
                        <div className="mono">{formatTimestamp(selectedRecord.valid_from)}</div>
                      </div>
                      <div className="field">
                        <span className="field-label">Invalid At</span>
                        <div className="mono">{formatTimestamp(selectedRecord.invalid_at)}</div>
                      </div>
                      <div className="field field-wide">
                        <span className="field-label">Superseded By</span>
                        <div className="mono">
                          {selectedRecord.superseded_by ?? '—'}
                        </div>
                      </div>
                      <div className="field field-wide">
                        <span className="field-label">Links</span>
                        <div className="mono">{formatList(selectedRecord.links, '无')}</div>
                      </div>
                      <div className="field field-wide">
                        <span className="field-label">Access Count</span>
                        <div className="mono">{selectedRecord.access_count}</div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </section>
          </div>

          <div className="stack">
            <section className="panel">
              <div className="panel-header">
                <h2 className="panel-title">安全写入</h2>
              </div>
              <div className="panel-body">
                <div className="preset-grid">
                  {WRITE_PRESETS.map((preset) => (
                    <button
                      key={preset.id}
                      type="button"
                      className="preset-chip"
                      disabled={writeLoading}
                      onClick={() => applyPreset(preset)}
                    >
                      {preset.label}
                    </button>
                  ))}
                </div>

                <div className="form-stack">
                  <div className="form-grid">
                    <div className="field">
                      <label className="field-label" htmlFor="write-name">
                        Name
                      </label>
                      <input
                        id="write-name"
                        type="text"
                        value={form.name}
                        onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                        placeholder="record-name"
                      />
                    </div>
                    <div className="field">
                      <label className="field-label" htmlFor="write-type">
                        Type
                      </label>
                      <select
                        id="write-type"
                        value={form.type}
                        onChange={(e) => {
                          const value = e.target.value;
                          if (value === 'decision' || value === 'experience' || value === 'fact') {
                            setForm((f) => ({ ...f, type: value }));
                          }
                        }}
                      >
                        <option value="decision">decision</option>
                        <option value="experience">experience</option>
                        <option value="fact">fact</option>
                      </select>
                    </div>
                    <div className="field field-wide">
                      <label className="field-label" htmlFor="write-abstract">
                        Abstract
                      </label>
                      <textarea
                        id="write-abstract"
                        value={form.abstract}
                        onChange={(e) => setForm((f) => ({ ...f, abstract: e.target.value }))}
                        placeholder="简短摘要"
                      />
                    </div>
                    <div className="field field-wide">
                      <label className="field-label" htmlFor="write-body">
                        Body
                      </label>
                      <textarea
                        id="write-body"
                        value={form.body}
                        onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
                        placeholder="正文内容"
                      />
                    </div>
                    <div className="field field-wide">
                      <label className="field-label" htmlFor="write-links">
                        Links（逗号分隔）
                      </label>
                      <input
                        id="write-links"
                        type="text"
                        value={form.linksInput}
                        onChange={(e) => setForm((f) => ({ ...f, linksInput: e.target.value }))}
                        placeholder="refund-sla-policy, adr-search-index"
                      />
                    </div>
                    <div className="field field-wide">
                      <label className="checkbox-field">
                        <input
                          type="checkbox"
                          checked={form.quarantine_on_poison}
                          onChange={(e) =>
                            setForm((f) => ({ ...f, quarantine_on_poison: e.target.checked }))
                          }
                        />
                        检测到投毒时自动隔离（quarantine_on_poison）
                      </label>
                    </div>
                  </div>

                  <div className="button-row">
                    <button
                      type="button"
                      className="button button-primary"
                      disabled={writeLoading || !form.name.trim() || !form.abstract.trim()}
                      onClick={submitForm}
                    >
                      {writeLoading ? '写入中…' : '提交写入'}
                    </button>
                  </div>
                </div>

                {writeError !== null && (
                  <div className="error-banner" style={{ marginTop: 18 }}>
                    写入失败：{writeError}
                  </div>
                )}

                {writeResult !== null && (
                  <div style={{ marginTop: 18 }}>
                    <div className="badge-row" style={{ marginBottom: 14 }}>
                      <span className={`badge ${getTrustBadgeClass(writeResult.trust_level)}`}>
                        {writeResult.trust_level}
                      </span>
                      <span className={`badge ${writeResult.written ? 'badge-trusted' : 'badge-failed'}`}>
                        {writeResult.written ? 'WRITTEN' : 'BLOCKED (NOT WRITTEN)'}
                      </span>
                      {writeResult.quarantined && (
                        <span className="badge badge-quarantined">QUARANTINED</span>
                      )}
                    </div>

                    <div className="form-grid" style={{ marginBottom: 14 }}>
                      <div className="field">
                        <span className="field-label">投毒原因</span>
                        <div className="mono">
                          {formatList(writeResult.poison_reasons, '无')}
                        </div>
                      </div>
                      <div className="field">
                        <span className="field-label">脱敏规则</span>
                        {writeResult.redactions_applied.length === 0 ? (
                          <div className="mono">未触发脱敏</div>
                        ) : (
                          <div className="badge-row">
                            {writeResult.redactions_applied.map((rule) => (
                              <span key={rule} className="badge badge-suspicious">{rule}</span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="form-grid">
                      <div className="field">
                        <span className="field-label">原始 Body</span>
                        <pre className="code-preview">{submittedBody || '（空）'}</pre>
                      </div>
                      <div className="field">
                        <span className="field-label">脱敏预览</span>
                        <pre className="code-preview">
                          {writeResult.redacted_body_preview || '（空）'}
                        </pre>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </section>
          </div>
        </div>

        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Sleep-Time 记忆整理</h2>
          </div>
          <div className="panel-body">
            <div className="button-row" style={{ marginBottom: 18 }}>
              <button
                type="button"
                className="button button-primary"
                disabled={sleepLoading}
                onClick={handleSleep}
              >
                🌙 触发 Sleep-Time 记忆整理
              </button>
            </div>

            {sleepError !== null && (
              <div className="error-banner" style={{ marginBottom: 18 }}>
                整理失败：{sleepError}
              </div>
            )}

            {sleepResult !== null && (
              <div className="stack">
                <div className="metric-grid">
                  <div className="metric-card">
                    <div className="metric-label">Duration</div>
                    <div className="metric-value">{sleepResult.duration_ms} ms</div>
                  </div>
                  <div className="metric-card">
                    <div className="metric-label">Actions</div>
                    <div className="metric-value">{sleepResult.actions.length}</div>
                  </div>
                </div>

                <div>
                  <h3 className="panel-kicker">确定性动作</h3>
                  {sleepResult.actions.length === 0 ? (
                    <div className="empty-state">本次无自动执行动作</div>
                  ) : (
                    <ul className="action-list">
                      {sleepResult.actions.map((action, index) => (
                        <li key={`${index}-${action.kind}-${action.target}`}>
                          <code>[{action.kind}] {action.target}: {action.detail}</code>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div>
                  <h3 className="panel-kicker" style={{ marginBottom: 12 }}>
                    待审批提案
                  </h3>
                  {sleepResult.proposals.length === 0 ? (
                    <div className="empty-state">暂无提案</div>
                  ) : (
                    <div className="form-stack">
                      {sleepResult.proposals.map((proposal) => (
                        <div
                          key={proposal.id}
                          className="panel"
                          style={{ background: 'rgba(4, 9, 15, 0.34)' }}
                        >
                          <div className="panel-body">
                            <div className="badge-row" style={{ marginBottom: 10 }}>
                              <span className="badge">{proposal.id}</span>
                              <span className="badge badge-suspicious">{proposal.kind}</span>
                            </div>
                            <div className="form-grid">
                              <div className="field field-wide">
                                <span className="field-label">Targets</span>
                                <div className="mono">{formatList(proposal.targets, '无')}</div>
                              </div>
                              <div className="field field-wide">
                                <span className="field-label">Reason</span>
                                <div style={{ fontSize: 12, lineHeight: 1.6 }}>{proposal.reason}</div>
                              </div>
                              <div className="field field-wide">
                                <span className="field-label">Evidence</span>
                                <div style={{ fontSize: 12, lineHeight: 1.6 }}>{proposal.evidence}</div>
                              </div>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </section>
  );
}
