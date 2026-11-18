import { useEffect, useRef, useState, type FormEvent } from 'react';
import { recallMemories, getRecord } from '../api/client';
import { RECALL_PRESETS } from '../presets/samples';
import type {
  MemoryLevel,
  MemoryRecordDetail,
  RecallHit,
  RecallRequest,
  RecallResponse,
} from '../types/memory';

const DATE_NULL_PLACEHOLDER = '';
const SCORE_FACTORS = [
  ['Relevance', 'relevance'], ['Recency', 'recency'],
  ['Weight', 'weight'], ['ACT-R Boost', 'access_boost'],
] as const;

function formatError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function formatPercent(value: number): string {
  return Number.isFinite(value) ? `${value.toFixed(1)}%` : '—';
}

function tokenPercent(part: number, whole: number): number {
  return whole > 0 ? Math.max(0, Math.min(100, (part / whole) * 100)) : 0;
}

function classNames(...items: (string | false | null | undefined)[]): string {
  return items.filter(Boolean).join(' ');
}

export default function ProgressiveRecallLab(): JSX.Element {
  const [query, setQuery] = useState<string>(RECALL_PRESETS[0]?.request.query ?? '');
  const [asOfInput, setAsOfInput] = useState<string>(
    RECALL_PRESETS[0]?.request.as_of ?? DATE_NULL_PLACEHOLDER,
  );
  const [includeQuarantined, setIncludeQuarantined] = useState<boolean>(
    RECALL_PRESETS[0]?.request.include_quarantined ?? false,
  );
  const [spotlighting, setSpotlighting] = useState<boolean>(
    RECALL_PRESETS[0]?.request.spotlighting ?? true,
  );

  const [recallResponse, setRecallResponse] = useState<RecallResponse | null>(null);
  const [recallLoading, setRecallLoading] = useState<boolean>(false);
  const [recallError, setRecallError] = useState<string | null>(null);

  const [selectedHitName, setSelectedHitName] = useState<string | null>(null);
  const [detailLevel, setDetailLevel] = useState<MemoryLevel>('abstract');
  const [detailRecord, setDetailRecord] = useState<MemoryRecordDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState<boolean>(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const recallSeqRef = useRef<number>(0);
  const detailSeqRef = useRef<number>(0);

  useEffect(() => () => {
    ++recallSeqRef.current;
    ++detailSeqRef.current;
  }, []);

  const buildRecallRequest = (): RecallRequest => ({
    query: query.trim(),
    as_of: asOfInput.trim() || null,
    include_quarantined: includeQuarantined,
    spotlighting: spotlighting,
  });

  const loadDetail = async (name: string, level: MemoryLevel): Promise<void> => {
    const ticket = ++detailSeqRef.current;
    setDetailLevel(level);
    setDetailLoading(true);
    setDetailError(null);
    setDetailRecord(null);

    try {
      const record = await getRecord(name, level);
      if (ticket === detailSeqRef.current) {
        setDetailRecord(record);
      }
    } catch (error: unknown) {
      if (ticket === detailSeqRef.current) {
        setDetailError(formatError(error));
      }
    } finally {
      if (ticket === detailSeqRef.current) {
        setDetailLoading(false);
      }
    }
  };

  const runRecall = async (request: RecallRequest): Promise<void> => {
    const ticket = ++recallSeqRef.current;
    ++detailSeqRef.current;
    setRecallLoading(true);
    setRecallError(null);
    setRecallResponse(null);
    setSelectedHitName(null);
    setDetailRecord(null);
    setDetailError(null);
    setDetailLoading(false);

    try {
      const response = await recallMemories(request);
      if (ticket === recallSeqRef.current) {
        setRecallResponse(response);
        const firstHit = response.hits[0] ?? null;
        setSelectedHitName(firstHit?.name ?? null);
        if (firstHit) {
          void loadDetail(firstHit.name, 'abstract');
        }
      }
    } catch (error: unknown) {
      if (ticket === recallSeqRef.current) {
        setRecallError(formatError(error));
        setRecallResponse(null);
        setSelectedHitName(null);
        setDetailRecord(null);
        setDetailError(null);
      }
    } finally {
      if (ticket === recallSeqRef.current) {
        setRecallLoading(false);
      }
    }
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const input = buildRecallRequest();
    if (input.query === '') {
      setRecallError('Query 不能为空');
      return;
    }
    void runRecall(input);
  };

  const applyPreset = (preset: (typeof RECALL_PRESETS)[number]): void => {
    setQuery(preset.request.query);
    setAsOfInput(preset.request.as_of ?? DATE_NULL_PLACEHOLDER);
    setIncludeQuarantined(preset.request.include_quarantined);
    setSpotlighting(preset.request.spotlighting);
    runRecall(preset.request);
  };

  const selectHit = (hit: RecallHit): void => {
    setSelectedHitName(hit.name);
    loadDetail(hit.name, 'abstract');
  };

  const switchLevel = (level: MemoryLevel): void => {
    if (selectedHitName) {
      loadDetail(selectedHitName, level);
    }
  };

  const selectedHit = recallResponse?.hits.find((h) => h.name === selectedHitName) ?? null;
  const maxima = { relevance: 0, recency: 0, weight: 0, access_boost: 0 };
  for (const hit of recallResponse?.hits ?? []) {
    for (const [, key] of SCORE_FACTORS) maxima[key] = Math.max(maxima[key], hit[key]);
  }

  return (
    <section className="progressive-recall-lab">
      <header className="page-intro">
        <div>
          <div className="eyebrow">Progressive Recall</div>
          <h1>渐进式召回实验室</h1>
          <p>
            通过真实后端验证渐进式上下文压缩、时间旅行回溯与投毒隔离。左侧控制召回请求，右侧查看命中记忆的三级内容。
          </p>
        </div>
      </header>

      <div className="recall-layout">
        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">召回控制</h2>
            </div>
            <div className="panel-body">
              <div className="preset-grid">
                {RECALL_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    className="preset-chip"
                    disabled={recallLoading}
                    onClick={() => applyPreset(preset)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>

              <form className="form-stack" onSubmit={handleSubmit}>
                <div className="form-grid">
                  <div className="field field-wide">
                    <label htmlFor="recall-query" className="field-label">
                      Query
                    </label>
                    <input
                      id="recall-query"
                      type="text"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="输入召回查询"
                      required
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="recall-as-of" className="field-label">
                      as_of (ISO 时间，留空为最新)
                    </label>
                    <input
                      id="recall-as-of"
                      type="text"
                      value={asOfInput}
                      onChange={(e) => setAsOfInput(e.target.value)}
                      placeholder="例如 2025-06-15T00:00:00Z"
                    />
                  </div>
                  <div className="field">
                    <span className="field-label">快捷时间</span>
                    <div className="button-row">
                      <button
                        type="button"
                        className="button button-secondary"
                        onClick={() => setAsOfInput(DATE_NULL_PLACEHOLDER)}
                      >
                        当前最新 (null)
                      </button>
                      <button
                        type="button"
                        className="button button-secondary"
                        onClick={() => setAsOfInput('2025-06-15T00:00:00Z')}
                      >
                        回溯至 2025-06-15
                      </button>
                    </div>
                  </div>
                </div>

                <div className="checkbox-row">
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={includeQuarantined}
                      onChange={(e) => setIncludeQuarantined(e.target.checked)}
                    />
                    include_quarantined
                  </label>
                  <label className="checkbox-field">
                    <input
                      type="checkbox"
                      checked={spotlighting}
                      onChange={(e) => setSpotlighting(e.target.checked)}
                    />
                    spotlighting
                  </label>
                </div>

                <div className="button-row">
                  <button
                    type="submit"
                    className="button button-primary"
                    disabled={recallLoading}
                  >
                    {recallLoading ? '召回中…' : '执行召回'}
                  </button>
                </div>
              </form>

              {recallError && (
                <div className="error-banner" role="alert">
                  {recallError}
                </div>
              )}
            </div>
          </section>

          {recallResponse && (
            <section className="panel">
              <div className="panel-header">
                <h2 className="panel-title">Token 仪表盘</h2>
              </div>
              <div className="panel-body">
                <div className="savings-callout">
                  <div>
                    <span className="metric-label">Progressive savings</span>
                    <strong>{formatPercent(recallResponse.token_budget.savings_pct)}</strong>
                  </div>
                  <span>
                    节省 {recallResponse.token_budget.tokens_saved.toLocaleString()} tokens
                  </span>
                </div>
                <div className="metric-grid">
                  <div className="metric-card">
                    <div className="metric-label">Full Store Tokens</div>
                    <div className="metric-value">
                      {recallResponse.token_budget.full_store_tokens.toLocaleString()}
                    </div>
                  </div>
                  <div className="metric-card">
                    <div className="metric-label">Progressive Context Tokens</div>
                    <div className="metric-value">
                      {recallResponse.token_budget.progressive_context_tokens.toLocaleString()}
                    </div>
                  </div>
                </div>

                <div className="budget-bars">
                  <div className="budget-item">
                    <div className="budget-meta">
                      <span>全量记忆灌入 (Full Store)</span>
                      <code>{recallResponse.token_budget.full_store_tokens.toLocaleString()} tokens</code>
                    </div>
                    <div className="progress-track" aria-hidden="true">
                      <div
                        className="progress-fill progress-full"
                        style={{
                          width: `${tokenPercent(
                            recallResponse.token_budget.full_store_tokens,
                            recallResponse.token_budget.full_store_tokens,
                          )}%`,
                        }}
                      />
                    </div>
                  </div>
                  <div className="budget-item">
                    <div className="budget-meta">
                      <span>渐进式披露（各命中摘要与首条大纲）</span>
                      <code>{recallResponse.token_budget.progressive_context_tokens.toLocaleString()} tokens</code>
                    </div>
                    <div className="progress-track" aria-hidden="true">
                      <div
                        className="progress-fill progress-progressive"
                        style={{
                          width: `${tokenPercent(
                            recallResponse.token_budget.progressive_context_tokens,
                            recallResponse.token_budget.full_store_tokens,
                          )}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>

                <div
                  className="badge-row"
                  style={{ marginTop: 16, alignItems: 'center' }}
                >
                  <span className="field-label" style={{ marginRight: 6 }}>
                    tokenizer:
                  </span>
                  <span className="badge">{recallResponse.tokenizer_used}</span>
                  <span className="field-label" style={{ marginRight: 6, marginLeft: 8 }}>
                    query_tokens:
                  </span>
                  {recallResponse.query_tokens.map((token, index) => (
                    <span key={`${index}-${token}`} className="badge">
                      {token}
                    </span>
                  ))}
                  <span className="field-label" style={{ marginRight: 6, marginLeft: 8 }}>
                    quarantined_filtered:
                  </span>
                  <span className="badge">{recallResponse.quarantined_filtered}</span>
                </div>
                <details>
                  <summary>查看实际召回上下文</summary>
                  <pre className="code-preview">{recallResponse.context_preview}</pre>
                </details>
              </div>
            </section>
          )}

          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">
                命中列表
                {recallResponse ? ` (${recallResponse.hits.length})` : ''}
              </h2>
            </div>
            <div className="panel-body">
              {recallLoading && <div className="notice">正在召回…</div>}
              {!recallLoading && !recallResponse && !recallError && (
                <div className="empty-state">点击预设或执行召回以查看命中</div>
              )}
              {!recallLoading && recallResponse && recallResponse.hits.length === 0 && (
                <div className="empty-state">无命中</div>
              )}

              {!recallLoading &&
                recallResponse &&
                recallResponse.hits.map((hit) => {
                  const isSelected = hit.name === selectedHitName;
                  return (
                    <article
                      key={hit.name}
                      className={classNames('panel', isSelected && 'selected-hit')}
                      onClick={() => selectHit(hit)}
                      role="button"
                      tabIndex={0}
                      aria-pressed={isSelected}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          selectHit(hit);
                        }
                      }}
                      style={{
                        marginBottom: 12,
                        cursor: 'pointer',
                        borderColor: isSelected ? 'var(--line-strong)' : undefined,
                      }}
                    >
                      <div className="panel-header">
                        <h3 className="panel-title">{hit.name}</h3>
                        <div className="badge-row">
                          <span className="badge">{hit.type}</span>
                          <span className="badge">{hit.status}</span>
                          <span className="badge">{hit.trust_level}</span>
                        </div>
                      </div>
                      <div className="panel-body">
                        <div className="metric-grid">
                          {SCORE_FACTORS.map(([label, key]) => (
                            <div key={label} className="metric-card">
                              <div className="metric-label">{label}</div>
                              <div className="metric-value" style={{ fontSize: 18 }}>
                                {hit[key].toFixed(3)}
                              </div>
                              <div
                                title="以本次命中中该指标的最大值为满格"
                                style={{
                                  height: 4,
                                  borderRadius: 2,
                                  background: 'rgba(127, 147, 165, 0.18)',
                                  overflow: 'hidden',
                                  marginTop: 6,
                                }}
                              >
                                <div
                                  style={{
                                    width: `${tokenPercent(hit[key], maxima[key])}%`,
                                    height: '100%',
                                    background: 'var(--cyan)',
                                  }}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                        <div
                          className="metric-card"
                          style={{ marginTop: 12 }}
                        >
                          <div className="metric-label">Score</div>
                          <div className="metric-value accent-text">
                            {hit.score.toFixed(4)}
                          </div>
                        </div>
                      </div>
                    </article>
                  );
                })}
            </div>
          </section>
        </div>

        <div className="stack">
          <section className="panel">
            <div className="panel-header">
              <h2 className="panel-title">
                {selectedHit ? selectedHit.name : '阅读器'}
              </h2>
              <div className="level-tabs">
                {(['abstract', 'outline', 'full'] as MemoryLevel[]).map((level) => (
                  <button
                    key={level}
                    type="button"
                    className={classNames(
                      'level-tab',
                      detailLevel === level && 'active',
                    )}
                    disabled={!selectedHitName || detailLoading}
                    onClick={() => switchLevel(level)}
                  >
                    {level === 'abstract' && 'L0 Abstract'}
                    {level === 'outline' && 'L1 Outline'}
                    {level === 'full' && 'L2 Full'}
                  </button>
                ))}
              </div>
            </div>
            <div className="panel-body">
              {!selectedHitName && (
                <div className="empty-state">在左侧选择一个命中以查看详情</div>
              )}
              {selectedHitName && detailLoading && (
                <div className="notice">加载 {detailLevel} 内容…</div>
              )}
              {selectedHitName && detailError && (
                <div className="error-banner" role="alert">
                  {detailError}
                </div>
              )}
              {selectedHitName && !detailLoading && !detailError && detailRecord && (
                <>
                  <div className="metric-grid" style={{ marginBottom: 18 }}>
                    <div className="metric-card">
                      <div className="metric-label">L0 abstract_tokens</div>
                      <div className="metric-value">
                        {detailRecord.abstract_tokens.toLocaleString()}
                      </div>
                    </div>
                    <div className="metric-card">
                      <div className="metric-label">L1 outline_tokens</div>
                      <div className="metric-value">
                        {detailRecord.outline_tokens.toLocaleString()}
                      </div>
                    </div>
                    <div className="metric-card">
                      <div className="metric-label">L2 full_tokens</div>
                      <div className="metric-value">
                        {detailRecord.full_tokens.toLocaleString()}
                      </div>
                    </div>
                    <div className="metric-card">
                      <div className="metric-label">token_savings_pct</div>
                      <div className="metric-value success">
                        {formatPercent(detailRecord.token_savings_pct)}
                      </div>
                    </div>
                  </div>

                  <div className="field">
                    <span className="field-label">
                      {detailRecord.level} content
                    </span>
                    <pre className="code-preview">{detailRecord.text}</pre>
                  </div>
                </>
              )}
            </div>
          </section>
        </div>
      </div>
    </section>
  );
}
