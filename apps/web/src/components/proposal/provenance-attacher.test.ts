import type { ProvenanceItem } from '@bluedev/shared-types';
import { describe, expect, it } from 'vitest';

import {
  attachProvenance,
  splitIntoSentences,
} from './provenance-attacher';

function item(overrides: Partial<ProvenanceItem>): ProvenanceItem {
  return {
    sentence_id: 'sid',
    section: 'excellence',
    content: '',
    source: 'ai-generated',
    agent_id: 'excellence_writer',
    llm_model: 'anthropic/claude-opus-4-7',
    llm_tokens: 1200,
    created_at: '2026-06-23T08:00:00Z',
    ...overrides,
  };
}

// ── Splitter parity with the Python recorder ───────────────────────────

describe('splitIntoSentences (TS port)', () => {
  it('returns empty list for empty input', () => {
    expect(splitIntoSentences('')).toEqual([]);
    expect(splitIntoSentences('   \n\n   ')).toEqual([]);
  });

  it('breaks prose on terminal punctuation', () => {
    expect(splitIntoSentences('First. Second! Third?')).toEqual([
      'First.',
      'Second!',
      'Third?',
    ]);
  });

  it('treats headings as standalone sentences', () => {
    const out = splitIntoSentences(
      '# Excellence\n\n## Sub\n\nBody first. Body second.',
    );
    expect(out[0]).toBe('# Excellence');
    expect(out[1]).toBe('## Sub');
    expect(out).toContain('Body first.');
    expect(out).toContain('Body second.');
  });

  it('breaks bullets into one sentence per item', () => {
    expect(splitIntoSentences('- First.\n- Second.\n- Third.')).toEqual([
      'First.',
      'Second.',
      'Third.',
    ]);
  });
});

// ── attachProvenance — happy paths ─────────────────────────────────────

describe('attachProvenance', () => {
  it('wraps a matched sentence in a provenance mark with item attrs', () => {
    const doc = attachProvenance(
      'Hello world.',
      [
        item({
          sentence_id: 'sent-1',
          content: 'Hello world.',
          agent_id: 'excellence_writer',
          llm_model: 'anthropic/claude-opus-4-7',
        }),
      ],
      { section: 'excellence' },
    );
    expect(doc.type).toBe('doc');
    const paragraph = doc.content?.[0];
    expect(paragraph?.type).toBe('paragraph');
    const text = paragraph?.content?.[0];
    expect(text?.text).toBe('Hello world.');
    expect(text?.marks?.[0]?.type).toBe('provenance');
    expect(text?.marks?.[0]?.attrs).toMatchObject({
      source: 'ai-generated',
      sentenceId: 'sent-1',
      agentId: 'excellence_writer',
      llmModel: 'anthropic/claude-opus-4-7',
    });
  });

  it('falls back to an unmarked text node when the sentence is not in items', () => {
    const doc = attachProvenance(
      'Unknown sentence.',
      [],
      { section: 'excellence' },
    );
    const text = doc.content?.[0]?.content?.[0];
    expect(text?.text).toBe('Unknown sentence.');
    expect(text?.marks).toBeUndefined();
  });

  it('ignores items whose section does not match', () => {
    const doc = attachProvenance(
      'Shared sentence.',
      [
        item({
          content: 'Shared sentence.',
          section: 'impact', // different section
        }),
      ],
      { section: 'excellence' },
    );
    const text = doc.content?.[0]?.content?.[0];
    expect(text?.marks).toBeUndefined();
  });

  it('builds a heading node + marks the heading text', () => {
    const doc = attachProvenance(
      '# Excellence',
      [item({ content: '# Excellence', sentence_id: 'h-1' })],
      { section: 'excellence' },
    );
    const heading = doc.content?.[0];
    expect(heading?.type).toBe('heading');
    expect(heading?.attrs?.level).toBe(1);
    expect(heading?.content?.[0]?.marks?.[0]?.attrs?.sentenceId).toBe('h-1');
  });

  it('builds a bullet list with one listItem per item', () => {
    const doc = attachProvenance(
      '- First.\n- Second.',
      [
        item({ content: 'First.', sentence_id: 'b-1' }),
        item({ content: 'Second.', sentence_id: 'b-2' }),
      ],
      { section: 'excellence' },
    );
    const list = doc.content?.[0];
    expect(list?.type).toBe('bulletList');
    expect(list?.content).toHaveLength(2);
    expect(list?.content?.[0]?.type).toBe('listItem');
    const firstText = list?.content?.[0]?.content?.[0]?.content?.[0];
    expect(firstText?.text).toBe('First.');
    expect(firstText?.marks?.[0]?.attrs?.sentenceId).toBe('b-1');
  });

  it('keeps unmatched sentences in the same paragraph with a single space joiner', () => {
    const doc = attachProvenance(
      'First. Second.',
      [item({ content: 'First.', sentence_id: 's-1' })],
      { section: 'excellence' },
    );
    const paragraph = doc.content?.[0];
    expect(paragraph?.content).toHaveLength(3); // text, space, text
    expect(paragraph?.content?.[0]?.text).toBe('First.');
    expect(paragraph?.content?.[1]?.text).toBe(' ');
    expect(paragraph?.content?.[2]?.text).toBe('Second.');
    expect(paragraph?.content?.[2]?.marks).toBeUndefined();
  });

  it('returns an empty doc for empty markdown', () => {
    const doc = attachProvenance('', [], { section: 'excellence' });
    expect(doc.type).toBe('doc');
    expect(doc.content).toEqual([]);
  });
});
