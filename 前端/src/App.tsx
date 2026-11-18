import { useEffect, useState } from 'react';
import {
  getHealth,
  getTrackingSnapshot,
  setNamespace,
  subscribeTracking,
} from './api/client';
import BenchmarkAndSecuritySuite from './components/BenchmarkAndSecuritySuite';
import MemoryGraphAndManage from './components/MemoryGraphAndManage';
import ProgressiveRecallLab from './components/ProgressiveRecallLab';
import type { Namespace } from './types/memory';

type PageId = 'recall' | 'manage' | 'benchmark';
type HealthState = 'checking' | 'online' | 'offline';

const PAGES: readonly { id: PageId; label: string; index: string }[] = [
  { id: 'recall', label: '渐进召回实验室', index: '01' },
  { id: 'manage', label: '图谱与记忆管理', index: '02' },
  { id: 'benchmark', label: '基准与安全评测', index: '03' },
];

const NAMESPACES: readonly Namespace[] = ['default', 'finance-agent', 'secops-agent'];

export default function App() {
  const [activePage, setActivePage] = useState<PageId>('recall');
  const [tracking, setTracking] = useState(getTrackingSnapshot);
  const [health, setHealth] = useState<HealthState>('checking');

  useEffect(() => subscribeTracking(setTracking), []);

  useEffect(() => {
    let mounted = true;
    setHealth('checking');
    getHealth()
      .then(() => {
        if (mounted) {
          setHealth('online');
        }
      })
      .catch(() => {
        if (mounted) {
          setHealth('offline');
        }
      });

    return () => {
      mounted = false;
    };
  }, [tracking.namespace]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-main">
          <div className="brand" aria-label="Dark Memory Lab">
            <span className="brand-mark" aria-hidden="true">DM</span>
            <div>
              <div className="brand-title">DARK MEMORY LAB</div>
              <div className="brand-subtitle">LONG-TERM MEMORY OBSERVATORY</div>
            </div>
          </div>

          <div className="topbar-controls">
            <label className="namespace-control">
              <span>Namespace</span>
              <select
                value={tracking.namespace}
                onChange={(event) => setNamespace(event.target.value as Namespace)}
                aria-label="切换 Namespace"
              >
                {NAMESPACES.map((namespace) => (
                  <option key={namespace} value={namespace}>{namespace}</option>
                ))}
              </select>
            </label>
            <div className={`health-pill health-${health}`} role="status">
              <span className="status-dot" aria-hidden="true" />
              API {health === 'checking' ? 'CHECKING' : health === 'online' ? 'ONLINE' : 'OFFLINE'}
            </div>
          </div>
        </div>

        <div className="topbar-secondary">
          <nav className="nav-tabs" aria-label="实验室页面">
            {PAGES.map((page) => (
              <button
                key={page.id}
                type="button"
                className={`nav-tab${activePage === page.id ? ' active' : ''}`}
                onClick={() => setActivePage(page.id)}
                aria-current={activePage === page.id ? 'page' : undefined}
              >
                <span>{page.index}</span>
                {page.label}
              </button>
            ))}
          </nav>

          <div className="tracking-strip" aria-label="请求追踪信息">
            <div className="tracking-item">
              <span className="tracking-label">Correlation ID</span>
              <code className="tracking-value" title={tracking.correlationId ?? undefined}>
                {tracking.correlationId ?? '—'}
              </code>
            </div>
            <div className="tracking-item">
              <span className="tracking-label">Latency</span>
              <code className="tracking-value accent-text">
                {tracking.latencyMs === null ? '—' : `${tracking.latencyMs.toFixed(2)} ms`}
              </code>
            </div>
          </div>
        </div>
      </header>

      <main key={tracking.namespace} className="page-shell">
        {activePage === 'recall' && <ProgressiveRecallLab />}
        {activePage === 'manage' && <MemoryGraphAndManage />}
        {activePage === 'benchmark' && <BenchmarkAndSecuritySuite />}
      </main>

      <footer className="app-footer">
        <span>MEMORY SYSTEM / LIVE CONTRACT</span>
        <span>127.0.0.1:8000 · VITE DEV PROXY</span>
      </footer>
    </div>
  );
}
