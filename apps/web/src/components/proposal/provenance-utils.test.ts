import { describe, expect, it } from 'vitest';

import { downgradeOnEdit, makeSentenceId } from './provenance-utils';

describe('makeSentenceId', () => {
  it('returns a string that starts with the s- prefix', () => {
    expect(makeSentenceId()).toMatch(/^s-/);
  });

  it('returns a different id on subsequent calls', () => {
    const ids = new Set([makeSentenceId(), makeSentenceId(), makeSentenceId()]);
    expect(ids.size).toBe(3);
  });
});

describe('downgradeOnEdit', () => {
  it('flips ai-generated to ai-edited', () => {
    expect(downgradeOnEdit('ai-generated')).toBe('ai-edited');
  });

  it('leaves every other source untouched', () => {
    expect(downgradeOnEdit('human')).toBe('human');
    expect(downgradeOnEdit('ai-edited')).toBe('ai-edited');
    expect(downgradeOnEdit('imported')).toBe('imported');
    expect(downgradeOnEdit('rag-retrieved')).toBe('rag-retrieved');
  });
});
