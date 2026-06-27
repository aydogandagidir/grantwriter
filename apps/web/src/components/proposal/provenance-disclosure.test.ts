import type { ProvenanceStatsResponse } from '@bluedev/shared-types';
import { describe, expect, it } from 'vitest';

import {
  formatDisclosure,
  summarisePercentages,
} from './provenance-disclosure';

function statsOf(per_source: { source: string; count: number }[]): ProvenanceStatsResponse {
  const total = per_source.reduce((acc, row) => acc + row.count, 0);
  return {
    total,
    per_source,
    per_agent: [],
    per_model: [],
  };
}

describe('summarisePercentages', () => {
  it('returns an empty record when the total is zero', () => {
    const result = summarisePercentages(statsOf([]));
    expect(result.total).toBe(0);
    expect(result.percentages).toEqual({});
  });

  it('emits integer percentages that sum to 100 using largest-remainder', () => {
    // 1/3, 1/3, 1/3 → floors are 33, 33, 33 (sum 99). One source gets +1.
    const result = summarisePercentages(
      statsOf([
        { source: 'human', count: 1 },
        { source: 'ai-generated', count: 1 },
        { source: 'ai-edited', count: 1 },
      ]),
    );
    const sum =
      (result.percentages.human ?? 0) +
      (result.percentages['ai-generated'] ?? 0) +
      (result.percentages['ai-edited'] ?? 0);
    expect(sum).toBe(100);
  });

  it('mirrors raw counts in the counts map', () => {
    const result = summarisePercentages(
      statsOf([
        { source: 'human', count: 10 },
        { source: 'ai-generated', count: 5 },
      ]),
    );
    expect(result.counts).toEqual({ human: 10, 'ai-generated': 5 });
  });
});

describe('formatDisclosure', () => {
  it('renders the empty-state when no provenance exists', () => {
    const text = formatDisclosure({ stats: statsOf([]), lang: 'en' });
    expect(text).toContain('AI Use Disclosure');
    expect(text).toContain('No sentence-level provenance');
  });

  it('lists each source with its count + percent + label', () => {
    const text = formatDisclosure({
      stats: statsOf([
        { source: 'human', count: 60 },
        { source: 'ai-generated', count: 30 },
        { source: 'ai-edited', count: 10 },
      ]),
      lang: 'en',
    });
    expect(text).toContain('Total sentences: 100');
    expect(text).toContain('Human-written: 60 sentences (60%)');
    expect(text).toContain('AI-generated: 30 sentences (30%)');
    expect(text).toContain('AI-edited: 10 sentences (10%)');
  });

  it('appends the agent + model lists when present', () => {
    const text = formatDisclosure({
      stats: {
        total: 5,
        per_source: [{ source: 'ai-generated', count: 5 }],
        per_agent: [{ source: 'excellence_writer', count: 3 }],
        per_model: [{ source: 'anthropic/claude-opus-4-7', count: 5 }],
      },
      lang: 'en',
    });
    expect(text).toContain('AI agents used:');
    expect(text).toContain('excellence_writer (3×)');
    expect(text).toContain('LLM models used:');
    expect(text).toContain('anthropic/claude-opus-4-7 (5×)');
  });

  it('localises labels + heading in Turkish', () => {
    const text = formatDisclosure({
      stats: statsOf([{ source: 'human', count: 1 }]),
      lang: 'tr',
    });
    expect(text).toContain('Önerideki yapay zekâ kullanım beyanı');
    expect(text).toContain('İnsan yazımı: 1 cümle (100%)');
  });

  it('keeps unknown sources in payload order behind the canonical ones', () => {
    const text = formatDisclosure({
      stats: statsOf([
        { source: 'imported', count: 1 },
        { source: 'custom-future-source', count: 1 },
        { source: 'human', count: 1 },
      ]),
      lang: 'en',
    });
    // canonical order: human first, then imported, then unknown
    const humanIdx = text.indexOf('Human-written');
    const importedIdx = text.indexOf('Imported');
    const customIdx = text.indexOf('custom-future-source');
    expect(humanIdx).toBeGreaterThan(-1);
    expect(humanIdx).toBeLessThan(importedIdx);
    expect(importedIdx).toBeLessThan(customIdx);
  });
});
