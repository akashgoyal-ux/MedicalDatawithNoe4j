import { useState } from 'react';
import { api } from '../api';

function BarChart({ data, labelKey, valueKey }) {
  const max = Math.max(...data.map((d) => d[valueKey]), 1);
  return (
    <div>
      {data.map((d) => (
        <div className="bar-row" key={d[labelKey]}>
          <span className="bar-label">{d[labelKey]}</span>
          <span className="bar" style={{ width: `${(d[valueKey] / max) * 100}%` }} />
          <span className="bar-val">{d[valueKey]}</span>
        </div>
      ))}
    </div>
  );
}

export default function SearchPage() {
  const [term, setTerm] = useState('Diabetes Mellitus');
  const [result, setResult] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function runSearch(e) {
    e?.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const [s, t] = await Promise.all([api.search(term), api.timeline(term)]);
      setResult(s);
      setTimeline(t.timeline);
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  }

  const ex = result?.expansion;
  return (
    <div className="page">
      <h2>Regulatory Document Search</h2>
      <p className="subtitle">
        Graph-expanded search: your term is expanded through the MeSH hierarchy so documents
        written with different agency terminology are still found.
      </p>
      <form className="searchrow" onSubmit={runSearch}>
        <input type="text" value={term} onChange={(e) => setTerm(e.target.value)}
               placeholder="Disease or indication, e.g. Diabetes Mellitus" />
        <button className="btn" disabled={loading}>{loading ? 'Searching…' : 'Search'}</button>
      </form>
      {error && <div className="error">{error}</div>}
      {result && (
        <>
          <div className="metrics">
            <div className="metric">
              <div className="value">{ex.matched_descriptor}</div>
              <div className="label">Matched MeSH descriptor</div>
            </div>
            <div className="metric">
              <div className="value">{ex.count}</div>
              <div className="label">Expanded terms</div>
            </div>
            <div className="metric">
              <div className="value">{result.baseline_count}</div>
              <div className="label">Exact-match recall</div>
            </div>
            <div className="metric">
              <div className="value">
                {result.expanded_count}{' '}
                {result.expanded_count > result.baseline_count && (
                  <span className="delta">+{result.expanded_count - result.baseline_count}</span>
                )}
              </div>
              <div className="label">Graph-expanded recall</div>
            </div>
          </div>
          <div className="grid2">
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Expanded terminology ({ex.count})</h3>
              <div className="terms">
                {ex.expanded_terms.map((t) => <div key={t}>{t}</div>)}
              </div>
            </div>
            <div>
              <div className="card">
                <h3 style={{ marginTop: 0 }}>Documents ({result.documents.length})</h3>
                <table>
                  <thead>
                    <tr><th>Document</th><th>Agency</th><th>Date</th></tr>
                  </thead>
                  <tbody>
                    {result.documents.map((d) => (
                      <tr key={d.docId}>
                        <td><a href={d.url} target="_blank" rel="noreferrer">{d.title}</a></td>
                        <td><span className="tag gray">{d.agency}</span></td>
                        <td>{d.date}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {timeline?.length > 0 && (
                <div className="card">
                  <h3 style={{ marginTop: 0 }}>Approvals over time</h3>
                  <BarChart data={timeline} labelKey="year" valueKey="approvals" />
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
