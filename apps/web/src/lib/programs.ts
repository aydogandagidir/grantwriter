import { Atom, Building2, Flag, Sparkles, Wheat } from 'lucide-react';
import type { ComponentType } from 'react';

import type { StatusBadgeVariant } from '@/components/ui/status-badge';

/**
 * Grant programs surfaced on the dashboard module grid.
 *
 * ``status`` controls the badge + whether the card is interactive:
 *
 * - ``active`` — module ships an end-to-end flow (form → draft → export).
 * - ``beta``   — module is wired but parts (templates, validators) are
 *                still iterating; the card is clickable.
 * - ``soon``   — placeholder, not yet wired. Card is rendered muted and
 *                non-interactive.
 *
 * Add new entries by appending to the array — the dashboard grid is
 * data-driven so no JSX edits are needed.
 */
export interface ProgramCard {
  id: string;
  icon: ComponentType<{ className?: string }>;
  /** i18n key under ``programs.<id>``: ``name``, ``tagline``, ``footer``. */
  i18nKey: string;
  status: StatusBadgeVariant;
  /** Optional href; ``soon`` items intentionally omit this. */
  href?: string;
  /** Subtle accent colour used for the icon background. */
  tone: 'red' | 'blue' | 'amber' | 'emerald' | 'violet';
}

export const PROGRAMS: ProgramCard[] = [
  {
    id: 'tubitak_1501',
    icon: Atom,
    i18nKey: 'tubitak_1501',
    status: 'active',
    href: '/proposals?programme=tubitak_1501',
    tone: 'red',
  },
  {
    id: 'tubitak_1507',
    icon: Atom,
    i18nKey: 'tubitak_1507',
    status: 'beta',
    href: '/proposals?programme=tubitak_1507',
    tone: 'red',
  },
  {
    id: 'kosgeb_arge',
    icon: Building2,
    i18nKey: 'kosgeb_arge',
    status: 'beta',
    href: '/proposals?programme=kosgeb_arge',
    tone: 'amber',
  },
  {
    id: 'horizon_eu',
    icon: Flag,
    i18nKey: 'horizon_eu',
    status: 'beta',
    href: '/proposals?programme=horizon_eu',
    tone: 'blue',
  },
  {
    id: 'cascade_nlnet',
    icon: Sparkles,
    i18nKey: 'cascade_nlnet',
    status: 'beta',
    href: '/proposals?programme=cascade_nlnet',
    tone: 'emerald',
  },
  {
    id: 'eurostars',
    icon: Wheat,
    i18nKey: 'eurostars',
    status: 'soon',
    tone: 'violet',
  },
];

const TONE_CLASSES: Record<ProgramCard['tone'], string> = {
  red: 'bg-red-500/10 text-red-600 dark:text-red-300',
  blue: 'bg-blue-500/10 text-blue-600 dark:text-blue-300',
  amber: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  emerald: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  violet: 'bg-violet-500/10 text-violet-700 dark:text-violet-300',
};

export function toneClass(tone: ProgramCard['tone']): string {
  return TONE_CLASSES[tone];
}
