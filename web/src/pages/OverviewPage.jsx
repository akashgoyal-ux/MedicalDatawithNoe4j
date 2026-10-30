import { useEffect, useState } from 'react';
import { api } from '../api';

const fmt = (n) => (n == null ? '—' : n.toLocaleString());

// Each layer card maps to live /stats counters (nothing hardcoded but labels).
const LAYERS = [
  {
    title: 'ATC + MeSH vocabularies',
    desc: 'WHO ATC 5-level tree + MeSH 2026 multi-parent DAG',
    stats: (s) => [
      ['ATC codes', (s.nodes.ATCLevel1 || 0) + (s.nodes.ATCLevel2 || 0) + (s.nodes.ATCLevel3 || 0)
        + (s.nodes.ATCLevel4 || 0) + (s.nodes.ATCLevel5 || 0)],
      ['MeSH descriptors', s.nodes.MeshDescriptor],
      ['Synonyms', s.nodes.MeshTerm],
      ['Hierarchy edges', s.edges.BROADER_THAN],
    ],
  },
  {
    title: 'Regulatory documents',
    desc: 'openFDA drug labels, MeSH-tagged via dictionary matching',
    stats: (s) => [
      ['Documents', s.nodes.RegulatoryDocument],
      ['MeSH tags', s.edges.MENTIONS],
      ['Agencies', s.nodes.Agency],
    ],
  },
  {
    title: 'FAERS safety graph',
    desc: 'Adverse-event reports, suspect drugs, MedDRA reactions',
    stats: (s) => [
      ['Reports', s.nodes.AdverseEventReport],
      ['Reactions', s.nodes.Reaction],
      ['Reaction links', s.edges.REPORTS_REACTION],
      ['MeSH cross-links', s.edges.MAPS_TO_MESH],
    ],
  },
  {
    title: 'ClinicalTrials.gov pipeline',
    desc: 'Trials joined to MeSH conditions, drugs, and sponsors',
    stats: (s) => [
      ['Trials', s.nodes.Trial],
      ['Sponsors', s.nodes.Sponsor],
      ['Condition links', s.edges.STUDIES],
      ['Drug interventions', s.edges.TESTS],
    ],
  },
  {
    title: 'RxNorm cross-walk',
    desc: 'Brand/ingredient aliases and ATC backfill via RxNav',
    stats: (s) => [
      ['Aliases', s.nodes.DrugAlias],
      ['Alias links', s.edges.ALIAS_OF],
    ],
  },
  {
    title: 'ICD-10-CM alignment',
    desc: 'Clinical codes aligned to active MeSH diseases',
    stats: (s) => [
      ['ICD-10 codes', s.nodes.ICD10Code],
      ['Alignments', s.edges.ALIGNS_WITH],
    ],
  },
  {
    title: 'Drug similarity',
    desc: 'Shared-indication + shared-reaction repurposing edges',
    stats: (s) => [
      ['SIMILAR_TO pairs', (s.edges.SIMILAR_TO || 0) / 2 | 0],
      ['Drugs', s.nodes.Drug],
    ],
  },
  {
    title: 'Ontology governance',
    desc: 'medical_graph.ttl (RDFS/rdflib) enforced by tests',
    stats: (s) => [
      ['Node classes', s.ontology.classes],
      ['Edge types', s.ontology.edgeTypes],
    ],
  },
  {
    title: 'Embeddings + vector search',
    desc: `Local embeddings, Neo4j vector indexes`,
    stats: (s) => [
      ['Embedded nodes', s.embeddedNodes],
      ['Vector indexes', s.vectorIndexes.length],
    ],
  },
  {
    title: 'GraphRAG assistant',
    desc: 'LangGraph agent with graph tools, fully local',
    stats: (s) => [
      ['Chat model', null, s.chatModel],
      ['Embed model', null, s.embedModel],
    ],
  },
];

export default function OverviewPage() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.stats().then(setStats).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="page">
      <h2>Platform Overview</h2>
      <p className="subtitle">
        Live status of every data layer in the knowledge graph — counts are queried
        from Neo4j in real time.
      </p>
      {error && <div className="error">{error}</div>}
      {!stats && !error && <div className="loading">Loading live graph statistics…</div>}
      <div className="dashgrid">
        {stats && LAYERS.map((layer) => (
          <div className="card dashcard" key={layer.title}>
            <div className="dashhead">
              <span className="check">✓</span>
              <div>
                <div className="dashtitle">{layer.title}</div>
                <div className="dashdesc">{layer.desc}</div>
              </div>
            </div>
            <div className="dashstats">
              {layer.stats(stats).map(([label, value, raw]) => (
                <div className="dashstat" key={label}>
                  <span className="v">{raw ?? fmt(value)}</span>
                  <span className="l">{label}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
