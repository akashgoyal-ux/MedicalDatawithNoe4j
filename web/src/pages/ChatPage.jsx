import { useRef, useState, useEffect } from 'react';
import { api } from '../api';

const SUGGESTIONS = [
  'Which drugs are indicated for diabetes and what reactions are reported for them?',
  'What does ATC code C03XA01 mean?',
  'Show me the clinical trial pipeline for hypertension',
  'What is the safety profile of ibuprofen?',
];

export default function ChatPage() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const logRef = useRef(null);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [messages, loading]);

  async function send(text) {
    const message = (text ?? input).trim();
    if (!message || loading) return;
    setInput('');
    setMessages((m) => [...m, { role: 'user', text: message }]);
    setLoading(true);
    try {
      const res = await api.chat(message);
      setMessages((m) => [...m, { role: 'bot', text: res.answer, tools: res.toolCalls }]);
    } catch (err) {
      setMessages((m) => [...m, { role: 'bot', text: `⚠️ ${err.message}` }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page">
      <h2>Ask Ria</h2>
      <p className="subtitle">
        A local GraphRAG assistant (Ollama + LangGraph) grounded in the knowledge graph —
        it can expand MeSH terms, search documents, decode ATC codes, and pull safety data.
      </p>
      <div className="card">
        <div className="chatlog" ref={logRef}>
          {messages.length === 0 && (
            <div>
              <p className="loading">Try one of these:</p>
              {SUGGESTIONS.map((s) => (
                <div key={s} style={{ margin: '6px 0' }}>
                  <button className="tag" style={{ cursor: 'pointer', border: 'none', fontSize: 13 }}
                          onClick={() => send(s)}>{s}</button>
                </div>
              ))}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role === 'user' ? 'user' : 'bot'}`}>
              {m.text}
              {m.tools?.length > 0 && (
                <div className="tools">
                  🔧 used: {m.tools.map((t) => `${t.tool}(${Object.values(t.args).join(', ')})`).join(' → ')}
                </div>
              )}
            </div>
          ))}
          {loading && <div className="msg bot loading">Thinking with the graph…</div>}
        </div>
        <form className="searchrow" style={{ marginBottom: 0 }}
              onSubmit={(e) => { e.preventDefault(); send(); }}>
          <input type="text" value={input} onChange={(e) => setInput(e.target.value)}
                 placeholder="Ask about drugs, diseases, trials, safety…" />
          <button className="btn" disabled={loading}>Send</button>
        </form>
      </div>
    </div>
  );
}
