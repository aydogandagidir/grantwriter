import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Tailwind-aware class joiner used by every shadcn component and most
 * page-level layouts. Resolves duplicate utility classes in favour of
 * the LAST one so `cn('p-2', condition && 'p-4')` does the right thing.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
