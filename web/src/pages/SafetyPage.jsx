import { useEffect, useState } from 'react';
import { api } from '../api';

export default function SafetyPage() {
  const [drugs, setDrugs] = useState([]);
  const [drug, setDrug] = useState('');
  const [data, setData] = useState(null);
  const [similar, setSimilar] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.drugs(true).then((list) => {
      setDrugs(list);
      if (list.length) setDrug(list[0]);
    }).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!drug) return;
    setLoading(true);
    setError(null);
    Promise.all([
      api.safety(drug),
      api.similar(drug).catch(() => null),
    ]).then(([s, sim]) => { setData(s); setSimilar(sim); })
      .catch((e) => { setError(e.message); setData(null); })
      .finally(() => setLoading(false));
  }, [drug]);

  return (
    <div className="page">
      <h2>Drug Safety Profile</h2>
      <p className="subtitle">
        FAERS adverse-event reports where the drug is the suspect, cross-linked to MeSH.
      </p>
      <div className="searchrow">
        <select value={drug} onChange={(e) => setDrug(e.target.value)} style={{ flex: 1 }}>
          {drugs.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>
      {error && <div className="error">{error}</div>}
      {loading && <div className="loading">Loading safety profile…</div>}
      {data && !loading && (
        <>
          <div className="metrics">
            <div className="metric"><div className="value">{data.summary.reports}</div>
              <div className="label">Reports (suspect)</div></div>
            <div className="metric"><div className="value">{data.summary.serious}</div>
              <div className="label">Serious</div></div>
            <div className="metric"><div className="value" style={{ color: data.summary.deaths ? 'var(--bad)' : undefined }}>{data.summary.deaths}</div>
              <div className="label">Deaths</div></div>
            <div className="metric"><div className="value">{data.summary.hospitalizations}</div>
              <div className="label">Hospitalizations</div></div>
          </div>
          <div className="grid2">
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Top reactions</h3>
              <table>
                <thead><tr><th>Reaction</th><th>Reports</th><th>Deaths</th><th>MeSH</th></tr></thead>
                <tbody>
                  {data.topReactions.map((r) => (
                    <tr key={r.reaction}>
                      <td>{r.reaction}</td>
                      <td>{r.reports}</td>
                      <td>{r.deaths > 0 ? <span className="tag red">{r.deaths}</span> : 0}</td>
                      <td>{r.meshDescriptor && <span className="tag">{r.meshDescriptor}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="card">
              <h3 style={{ marginTop: 0 }}>Similar drugs (repurposing candidates)</h3>
              {similar ? (
                <table>
                  <thead><tr><th>Drug</th><th>Shared indications</th><th>Shared reactions</th></tr></thead>
                  <tbody>
                    {similar.similar.map((s) => (
                      <tr key={s.drug}>
                        <td>{s.drug}</td><td>{s.sharedIndications}</td><td>{s.sharedReactions}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="loading">No similarity edges for this drug.</p>}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
