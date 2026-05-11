import { describe, expect, it } from 'vitest';

import { cn } from './utils';

describe('cn', () => {
  it('joins truthy class names', () => {
    expect(cn('a', 'b', 'c')).toBe('a b c');
  });

  it('drops falsy values', () => {
    const flag = false;
    expect(cn('a', flag && 'b', 'c', null, undefined)).toBe('a c');
  });

  it('resolves tailwind conflicts in favour of the last value', () => {
    // twMerge collapses `p-2` and `p-4` into the last one — important
    // for our `cn(base, conditional && override)` pattern.
    expect(cn('p-2', 'p-4')).toBe('p-4');
    expect(cn('text-sm font-medium', 'text-lg')).toBe('font-medium text-lg');
  });

  it('accepts object + array inputs (clsx semantics)', () => {
    expect(cn('a', { b: true, c: false }, ['d', 'e'])).toBe('a b d e');
  });
});
