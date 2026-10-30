const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

async function get(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const res = await fetch(`${API}${path}${qs ? `?${qs}` : ''}`);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export const api = {
  health: () => get('/health'),
  stats: () => get('/stats'),
  search: (term, depth = 13) => get('/search', { term, depth, limit: 50 }),
  expand: (term) => get('/expand', { term }),
  timeline: (term) => get('/timeline', { term }),
  decodeAtc: (code) => get(`/atc/${encodeURIComponent(code)}`),
  safety: (drug) => get(`/safety/${encodeURIComponent(drug)}`),
  drugs: (withReports = false) => get('/drugs', withReports ? { with_reports: true } : {}),
  trials: (term, depth = 5) => get('/trials', { term, depth, limit: 50 }),
  similar: (drug) => get(`/similar/${encodeURIComponent(drug)}`),
  vsearch: (q) => get('/vsearch', { q }),
  chat: async (message) => {
    const res = await fetch(`${API}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Chat failed (${res.status})`);
    }
    return res.json();
  },
};
