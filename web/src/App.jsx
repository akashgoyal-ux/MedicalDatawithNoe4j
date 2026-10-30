import { useState } from 'react';
import OverviewPage from './pages/OverviewPage';
import SearchPage from './pages/SearchPage';
import SafetyPage from './pages/SafetyPage';
import PipelinePage from './pages/PipelinePage';
import AtcPage from './pages/AtcPage';
import ChatPage from './pages/ChatPage';

const NAV = [
  { id: 'overview', label: 'Overview', component: OverviewPage },
  { id: 'search', label: 'Search', component: SearchPage },
  { id: 'pipeline', label: 'Pipeline', component: PipelinePage },
  { id: 'safety', label: 'Safety', component: SafetyPage },
  { id: 'atc', label: 'ATC Decoder', component: AtcPage },
  { id: 'chat', label: 'Assistant', component: ChatPage },
];

export default function App() {
  const [page, setPage] = useState('overview');
  const Active = NAV.find((n) => n.id === page).component;
  return (
    <>
      <header className="topbar">
        <div className="brand">
          Ria<span>·</span>Prototype
          <small>Regulatory intelligence on Neo4j — MeSH · ATC · FAERS · Trials</small>
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <button key={n.id} className={page === n.id ? 'active' : ''}
                    onClick={() => setPage(n.id)}>
              {n.label}
            </button>
          ))}
        </nav>
      </header>
      <Active />
    </>
  );
}
