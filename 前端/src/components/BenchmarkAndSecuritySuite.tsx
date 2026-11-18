import { useEffect, useRef, useState } from 'react';
import { runBenchmark } from '../api/client';
import type { BenchmarkPlane, BenchmarkResponse } from '../types/memory';

type FilterKey = 'all' | BenchmarkPlane;

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'retrieval', label: '检索' },
  { key: 'safety', label: '安全' },
  { key: 'context', label: '上下文' },
];

function formatPercent(value: number | undefined): string {
  if (value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return `${value.toFixed(1)}%`;
}

function formatLatency(value: number | undefined): string {
  if (value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return `${value.toFixed(1)} ms`;
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

export default function BenchmarkAndSecuritySuite(): JSX.Element {
  const [response, setResponse] = useState<BenchmarkResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterKey>('all');
  const requestIdRef = useRef(0);

  useEffect(() => () => {
    ++requestIdRef.current;
  }, []);

  const filteredCases =
    response?.cases.filter((c) => filter === 'all' || c.plane === filter) ?? [];

  async function handleRunBenchmark(): Promise<void> {
    const requestId = (requestIdRef.current += 1);
    setLoading(true);
    setError(null);
    setResponse(null);

    try {
      const next = await runBenchmark();
      if (requestId === requestIdRef.current) {
        setResponse(next);
      }
    } catch (err) {
      if (requestId === requestIdRef.current) {
        setError(getErrorMessage(err));
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }

  return (
    <section className="benchmark-suite">
      <header className="page-intro">
        <div>
          <div className="eyebrow">Benchmark &amp; Security Suite</div>
          <h1>基准评测与安全套件</h1>
          <p>
            一键运行 24 条覆盖 CJK 检索、投毒防御、误报控制、Token 节省与延迟分布的记忆基准用例。
          </p>
        </div>
        <button
          type="button"
          className="button button-primary"
          onClick={handleRunBenchmark}
          disabled={loading}
        >
          {loading ? '正在运行 24 用例评测…' : '🚀 运行 24 用例记忆与安全基准评测'}
        </button>
      </header>

      {error !== null && (
        <div className="error-banner" role="alert">
          评测请求失败：{error}
        </div>
      )}

      {response === null ? (
        !loading && !error && <div className="empty-state">
          <div>
            <p>尚未运行基准评测</p>
            <p className="metric-subtext">点击上方按钮开始 24 用例全量评测</p>
          </div>
        </div>
      ) : (
        <div className="stack">
          <div className="panel">
            <div className="panel-header">
              <h2 className="panel-title">核心指标</h2>
            </div>
            <div className="panel-body">
              <div className="metric-grid benchmark-metrics">
                <div className="metric-card">
                  <div className="metric-label">CJK Recall@3 legacy → enhanced</div>
                  <div className="metric-value success">
                    {formatPercent(response.cjk_recall_at_3_legacy_pct)}
                    {' → '}
                    {formatPercent(response.cjk_recall_at_3_enhanced_pct)}
                  </div>
                  <div className="metric-subtext">中文混合检索前三位召回率对比</div>
                </div>

                <div className="metric-card">
                  <div className="metric-label">Poison Defense Rate</div>
                  <div className="metric-value success">
                    {formatPercent(response.poison_defense_rate_pct)}
                  </div>
                  <div className="metric-subtext">投毒攻击被正确拦截的比例</div>
                </div>

                <div className="metric-card">
                  <div className="metric-label">Benign False Positive Rate</div>
                  <div
                    className={`metric-value ${
                      response.benign_false_positive_rate_pct <= 5 ? 'success' : ''
                    }`}
                  >
                    {formatPercent(response.benign_false_positive_rate_pct)}
                  </div>
                  <div className="metric-subtext">正常样本被误判为投毒的比率</div>
                </div>

                <div className="metric-card">
                  <div className="metric-label">Redaction Coverage</div>
                  <div className="metric-value">{formatPercent(response.redaction_coverage_pct)}</div>
                  <div className="metric-subtext">凭证及个人信息脱敏覆盖率</div>
                </div>

                <div className="metric-card">
                  <div className="metric-label">Avg Token Savings</div>
                  <div className="metric-value success">
                    {formatPercent(response.avg_token_savings_pct)}
                  </div>
                  <div className="metric-subtext">渐进式上下文平均 Token 节省</div>
                </div>

                <div className="metric-card">
                  <div className="metric-label">Latency Avg / P95</div>
                  <div className="metric-value">
                    {formatLatency(response.avg_latency_ms)}
                    {' / '}
                    {formatLatency(response.p95_latency_ms)}
                  </div>
                  <div className="metric-subtext">平均延迟与 95 分位延迟</div>
                </div>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-header">
              <h2 className="panel-title">用例明细</h2>
              <div className="filter-row" role="group" aria-label="用例分类筛选">
                {FILTERS.map(({ key, label }) => (
                  <button
                    key={key}
                    type="button"
                    className={`filter-chip ${filter === key ? 'active' : ''}`}
                    onClick={() => setFilter(key)}
                    aria-pressed={filter === key}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="panel-body">
              {filteredCases.length === 0 ? (
                <div className="empty-state">当前筛选条件下没有匹配的用例</div>
              ) : (
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th scope="col">用例 ID</th>
                        <th scope="col">分类</th>
                        <th scope="col">描述</th>
                        <th scope="col">期望</th>
                        <th scope="col">实际</th>
                        <th scope="col">结果</th>
                        <th scope="col" className="numeric">
                          延迟
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredCases.map((c) => (
                        <tr key={c.case_id}>
                          <td className="mono">{c.case_id}</td>
                          <td>
                            <span className="badge">{c.category}</span>
                          </td>
                          <td>{c.description}</td>
                          <td className="mono">{c.expected}</td>
                          <td className="mono">{c.actual}</td>
                          <td>
                            <span
                              className={`badge ${
                                c.passed ? 'badge-passed' : 'badge-failed'
                              }`}
                            >
                              {c.passed ? '通过' : '失败'}
                            </span>
                          </td>
                          <td className="numeric mono">
                            {formatLatency(c.latency_ms)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
