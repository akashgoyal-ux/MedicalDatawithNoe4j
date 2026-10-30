import { useState } from 'react';
import { api } from '../api';

export default function AtcPage() {
  const [code, setCode] = useState('C03XA01');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  async function run(e) {
    e?.preventDefault();
    setLoading(true);
    setError(null);
    try {
      setData(await api.decodeAtc(code.trim()));
    } catch (err) {
      setError(err.message);
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <h2>ATC Code Decoder</h2>
      <p className="subtitle">
        The WHO Anatomical Therapeutic Chemical classification — a drug's five-level biography.
      </p>
      <form className="searchrow" onSubmit={run}>
        <input type="text" value={code} onChange={(e) => setCode(e.target.value)}
               placeholder="ATC code, e.g. C03XA01" />
        <button className="btn" disabled={loading}>{loading ? 'Decoding…' : 'Decode'}</button>
      </form>
      {error && <div className="error">{error}</div>}
      {data && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Biography of {data.code}</h3>
          <ul className="ladder">
            {data.breakdown.map((s) => (
              <li key={s.code}>
                <span className="lvl">Level {s.level}</span><br />
                <code>{s.code}</code>{s.name}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
