import { useState } from 'react';
import { api } from '../api';

const PHASE_LABEL = {
  EARLY_PHASE1: 'Early P1', PHASE1: 'Phase 1', PHASE2: 'Phase 2',
  PHASE3: 'Phase 3', PHASE4: 'Phase 4', NA: 'N/A',
};

export default function PipelinePage() {
  const [term, setTerm] = useState('Diabetes Mellitus');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function run(e) {
    e?.preventDefault();
    setLoading(true);
    setError(null);
    try {
      setData(await api.trials(term));
    } catch (err) {
      setError(err.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  const byPhase = data
    ? Object.entries(data.trials.reduce((acc, t) => {
        const p = PHASE_LABEL[t.phase] || t.phase || 'N/A';
        acc[p] = (acc[p] || 0) + 1;
        return acc;
      }, {})).map(([phase, n]) => ({ phase, n }))
    : [];

  return (
    <div className="page">
      <h2>Pipeline Intelligence</h2>
      <p className="subtitle">
        ClinicalTrials.gov studies joined to the MeSH graph — what is being tested,
        by whom, and at what phase.
      </p>
      <form className="searchrow" onSubmit={run}>
        <input type="text" value={term} onChange={(e) => setTerm(e.target.value)}
               placeholder="Disease, e.g. Diabetes Mellitus" />
        <button className="btn" disabled={loading}>{loading ? 'Loading…' : 'Explore'}</button>
      </form>
      {error && <div className="error">{error}</div>}
      {data && (
        <>
          <div className="metrics">
            <div className="metric"><div className="value">{data.matched_descriptor}</div>
              <div className="label">MeSH descriptor</div></div>
            <div className="metric"><div className="value">{data.count}</div>
              <div className="label">Trials (expanded)</div></div>
            {byPhase.map((p) => (
              <div className="metric" key={p.phase}>
                <div className="value">{p.n}</div>
                <div className="label">{p.phase}</div>
              </div>
            ))}
          </div>
          <div className="card">
            <table>
              <thead>
                <tr><th>Trial</th><th>Phase</th><th>Status</th><th>Sponsor</th><th>Drugs tested</th></tr>
              </thead>
              <tbody>
                {data.trials.map((t) => (
                  <tr key={t.nctId}>
                    <td>
                      <a href={`https://clinicaltrials.gov/study/${t.nctId}`}
                         target="_blank" rel="noreferrer">{t.title}</a>
                      <div style={{ color: 'var(--muted)', fontSize: 12 }}>{t.nctId} · {t.startDate}</div>
                    </td>
                    <td><span className="tag">{PHASE_LABEL[t.phase] || t.phase || 'N/A'}</span></td>
                    <td><span className="tag gray">{t.status}</span></td>
                    <td>{t.sponsor}</td>
                    <td>{t.drugsTested.map((d) => <span key={d} className="tag">{d}</span>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
