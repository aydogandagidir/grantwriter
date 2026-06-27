/**
 * Build the AI-usage disclosure block from a proposal's provenance
 * stats. This is the text the proposal author will paste into
 * Horizon Europe Part B "AI Use" section (page 32 of the standard
 * proposal template) or the equivalent TÜBİTAK declaration.
 *
 * Pure / testable: no DOM, no React imports — feeds directly into
 * a clipboard call from the surrounding panel.
 */

import type { ProvenanceSourceCount, ProvenanceStatsResponse } from '@bluedev/shared-types';

export type ProvenanceSource =
  | 'human'
  | 'ai-generated'
  | 'ai-edited'
  | 'imported'
  | 'rag-retrieved'
  | (string & {}); // allow forward-compat sources without losing literal hints

export interface DisclosurePercentages {
  total: number;
  /** Source → integer percent of total sentences (0–100). Sums to ≤100. */
  percentages: Record<string, number>;
  /** Source → raw sentence count. */
  counts: Record<string, number>;
}

/** Canonical display order — keeps the disclosure block deterministic. */
const SOURCE_ORDER: ProvenanceSource[] = [
  'human',
  'ai-generated',
  'ai-edited',
  'imported',
  'rag-retrieved',
];

/**
 * Compute integer percentages with the largest-remainder method, so
 * the rounded percentages still sum to 100 when the totals do. Avoids
 * the "99 % shown but 100 % real" rounding bug naive Math.round() hits.
 */
export function summarisePercentages(
  stats: Pick<ProvenanceStatsResponse, 'total' | 'per_source'>,
): DisclosurePercentages {
  const counts: Record<string, number> = {};
  for (const row of stats.per_source) {
    counts[row.source] = row.count;
  }

  if (stats.total <= 0) {
    return { total: 0, percentages: {}, counts };
  }

  const raws: { source: string; raw: number }[] = stats.per_source.map((row) => ({
    source: row.source,
    raw: (row.count * 100) / stats.total,
  }));

  const floors = raws.map((entry) => ({
    source: entry.source,
    floor: Math.floor(entry.raw),
    remainder: entry.raw - Math.floor(entry.raw),
  }));

  // Largest-remainder: distribute the leftover until we hit 100.
  const floorSum = floors.reduce((acc, entry) => acc + entry.floor, 0);
  let leftover = 100 - floorSum;
  const ranked = [...floors].sort((a, b) => b.remainder - a.remainder);
  for (let i = 0; i < ranked.length && leftover > 0; i += 1) {
    const entry = ranked[i];
    if (entry === undefined) break;
    entry.floor += 1;
    leftover -= 1;
  }

  const percentages: Record<string, number> = {};
  for (const entry of floors) {
    percentages[entry.source] = entry.floor;
  }
  return { total: stats.total, percentages, counts };
}

/** Localised labels for each source. */
const LABELS_EN: Record<string, string> = {
  human: 'Human-written',
  'ai-generated': 'AI-generated',
  'ai-edited': 'AI-edited',
  imported: 'Imported',
  'rag-retrieved': 'RAG-retrieved (from knowledge base)',
};
const LABELS_TR: Record<string, string> = {
  human: 'İnsan yazımı',
  'ai-generated': 'AI tarafından üretilen',
  'ai-edited': 'AI tarafından düzenlenen',
  imported: 'Dış kaynaktan alınan',
  'rag-retrieved': 'Bilgi tabanından çekilen (RAG)',
};

function labelFor(source: string, lang: 'en' | 'tr'): string {
  return (lang === 'tr' ? LABELS_TR : LABELS_EN)[source] ?? source;
}

function uniqueSources(stats: Pick<ProvenanceStatsResponse, 'per_source'>): string[] {
  const seen = new Set<string>();
  const ordered: string[] = [];
  // Canonical sources first.
  for (const known of SOURCE_ORDER) {
    if (stats.per_source.some((row: ProvenanceSourceCount) => row.source === known)) {
      ordered.push(known);
      seen.add(known);
    }
  }
  // Forward-compat: any unknown source appears after, in payload order.
  for (const row of stats.per_source) {
    if (!seen.has(row.source)) {
      ordered.push(row.source);
      seen.add(row.source);
    }
  }
  return ordered;
}

export interface FormatDisclosureInput {
  stats: ProvenanceStatsResponse;
  lang?: 'en' | 'tr';
}

/**
 * Build the disclosure text. Format:
 *
 *   {Heading}
 *   - Human-written: 42 sentences (37 %)
 *   - AI-generated: 30 sentences (26 %)
 *   ...
 *   AI tools used:
 *   - anthropic/claude-opus-4-7 — Excellence Writer
 *   - anthropic/claude-sonnet-4-6 — Hallucination Hunter
 *
 * Heading is locale-dependent so the author can paste straight into
 * the application's language.
 */
export function formatDisclosure({
  stats,
  lang = 'en',
}: FormatDisclosureInput): string {
  const tr = lang === 'tr';
  const heading = tr
    ? 'Önerideki yapay zekâ kullanım beyanı'
    : 'AI Use Disclosure for this proposal';
  const summary = summarisePercentages(stats);

  const lines: string[] = [heading, ''];
  if (summary.total === 0) {
    lines.push(
      tr
        ? 'Bu öneride henüz cümle düzeyinde köken kaydı bulunmuyor.'
        : 'No sentence-level provenance has been recorded for this proposal yet.',
    );
    return lines.join('\n');
  }

  const totalLine = tr
    ? `Toplam cümle sayısı: ${summary.total}`
    : `Total sentences: ${summary.total}`;
  lines.push(totalLine, '');

  for (const source of uniqueSources(stats)) {
    const count = summary.counts[source] ?? 0;
    const pct = summary.percentages[source] ?? 0;
    const label = labelFor(source, lang);
    const sentenceWord = tr ? 'cümle' : count === 1 ? 'sentence' : 'sentences';
    lines.push(`- ${label}: ${count} ${sentenceWord} (${pct}%)`);
  }

  if (stats.per_agent.length > 0) {
    lines.push('', tr ? 'Kullanılan AI ajanları:' : 'AI agents used:');
    for (const entry of stats.per_agent) {
      lines.push(`- ${entry.source}${entry.count > 1 ? ` (${entry.count}×)` : ''}`);
    }
  }
  if (stats.per_model.length > 0) {
    lines.push('', tr ? 'Kullanılan LLM modelleri:' : 'LLM models used:');
    for (const entry of stats.per_model) {
      lines.push(`- ${entry.source}${entry.count > 1 ? ` (${entry.count}×)` : ''}`);
    }
  }

  return lines.join('\n');
}

export { SOURCE_ORDER, labelFor };
