'use client';

import {
  Atom,
  BookOpen,
  Building2,
  FileText,
  Home,
  KeyRound,
  Mail,
  Plus,
  Receipt,
  ScrollText,
  Sparkles,
  TrendingUp,
  Users,
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { ComponentType } from 'react';

import { StatusBadge, type StatusBadgeVariant } from '@/components/ui/status-badge';
import { Link, usePathname } from '@/i18n/navigation';
import { cn } from '@/lib/utils';

/**
 * App-shell sidebar — sticky, sectioned navigation with availability
 * badges. Linear / Vercel aesthetic: square corners, single hairline
 * border, no shadows. Density is the point: every row earns its line
 * of vertical space.
 *
 * Anatomy
 *
 *   ┌─────────────────────────┐
 *   │ [logo] bluedev  [G…]    │  brand header (border-b)
 *   │ tenant name             │
 *   ├─────────────────────────┤
 *   │ ÖZET                    │  caps eyebrow
 *   │  ▸ Modül Merkezi        │  row (active = primary tint)
 *   │                         │
 *   │ BAŞVURULAR              │
 *   │  ▸ Tüm Başvurular       │
 *   │  + Yeni Başvuru         │
 *   │                         │
 *   │ PROGRAMLAR              │
 *   │  ▸ TÜBİTAK 1501  [...]  │  StatusBadge only when ≠ active
 *   │                         │
 *   │ YÖNETİM                 │
 *   ├─────────────────────────┤
 *   │ admin@bluedev.dev       │  footer
 *   │ Rol: Sahip              │
 *   └─────────────────────────┘
 *
 * Sticky: ``sticky top-0 h-screen`` so the sidebar stays put while the
 * main column scrolls — the layout flex container provides the
 * positioning context.
 *
 * Visibility: ``md:flex hidden`` on desktop; ``MobileNav`` renders the
 * same sections in a drawer on narrow viewports.
 */

export interface NavItem {
  href?: string;
  i18nKey: string;
  icon: ComponentType<{ className?: string }>;
  status?: StatusBadgeVariant;
  adminOnly?: boolean;
}

export interface NavSection {
  i18nKey: string;
  items: NavItem[];
}

export const NAV_SECTIONS: NavSection[] = [
  {
    i18nKey: 'overview',
    items: [
      { href: '/dashboard', i18nKey: 'dashboard', icon: Home },
    ],
  },
  {
    i18nKey: 'proposalsGroup',
    items: [
      { href: '/proposals', i18nKey: 'proposals', icon: FileText },
      { href: '/proposals/new', i18nKey: 'newProposal', icon: Plus },
    ],
  },
  {
    i18nKey: 'programs',
    items: [
      {
        href: '/proposals?programme=tubitak_1501',
        i18nKey: 'tubitak_1501Short',
        icon: Atom,
        status: 'active',
      },
      {
        href: '/proposals?programme=tubitak_1507',
        i18nKey: 'tubitak_1507Short',
        icon: Atom,
        status: 'beta',
      },
      {
        href: '/proposals?programme=kosgeb_arge',
        i18nKey: 'kosgeb_argeShort',
        icon: Building2,
        status: 'beta',
      },
      {
        href: '/proposals?programme=horizon_eu',
        i18nKey: 'horizon_euShort',
        icon: BookOpen,
        status: 'beta',
      },
      {
        href: '/proposals?programme=cascade_nlnet',
        i18nKey: 'cascade_nlnetShort',
        icon: Sparkles,
        status: 'beta',
      },
    ],
  },
  {
    i18nKey: 'admin',
    items: [
      {
        href: '/settings/llm-config',
        i18nKey: 'llmConfig',
        icon: KeyRound,
      },
      {
        href: '/settings/members',
        i18nKey: 'members',
        icon: Users,
        adminOnly: true,
      },
      {
        href: '/settings/invitations',
        i18nKey: 'invitations',
        icon: Mail,
        adminOnly: true,
      },
      {
        href: '/settings/audit',
        i18nKey: 'audit',
        icon: ScrollText,
        adminOnly: true,
      },
      {
        href: '/settings/usage',
        i18nKey: 'usage',
        icon: TrendingUp,
        adminOnly: true,
      },
      {
        href: '/settings/billing',
        i18nKey: 'billing',
        icon: Receipt,
        adminOnly: true,
      },
    ],
  },
];

export function Sidebar({
  role,
  tenantName,
  email,
}: {
  role: 'owner' | 'admin' | 'member' | 'viewer';
  tenantName: string;
  email: string | null;
}) {
  const t = useTranslations('nav');
  const pathname = usePathname();
  const isAdmin = role === 'owner' || role === 'admin';

  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-card md:flex">
      {/* Brand + tenant header */}
      <div className="border-b px-4 py-3.5">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center bg-primary text-primary-foreground">
            <FileText className="h-3.5 w-3.5" />
          </div>
          <span className="text-sm font-semibold tracking-tight">bluedev</span>
          <span className="bg-muted px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground">
            GrantWriter
          </span>
        </div>
        <p className="mt-2 truncate text-xs text-foreground" title={tenantName}>
          {tenantName}
        </p>
      </div>

      {/* Sectioned nav */}
      <nav className="flex-1 space-y-4 overflow-y-auto px-2 py-3">
        {NAV_SECTIONS.map((section) => (
          <Section
            key={section.i18nKey}
            section={section}
            pathname={pathname}
            isAdmin={isAdmin}
            t={t}
          />
        ))}
      </nav>

      {/* Footer: identity context */}
      <div className="border-t px-4 py-2.5">
        <p className="truncate text-[11px] text-muted-foreground" title={email ?? ''}>
          {email}
        </p>
        <p className="mt-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">
          {t('roleLabel')}: {t(`roles.${role}`)}
        </p>
      </div>
    </aside>
  );
}

function Section({
  section,
  pathname,
  isAdmin,
  t,
  onNavigate,
}: {
  section: NavSection;
  pathname: string;
  isAdmin: boolean;
  t: (key: string) => string;
  onNavigate?: () => void;
}) {
  const visibleItems = section.items.filter(
    (item) => !item.adminOnly || isAdmin,
  );
  if (visibleItems.length === 0) return null;

  return (
    <div>
      <div className="mb-1 px-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t(section.i18nKey)}
      </div>
      <ul>
        {visibleItems.map((item) => (
          <li key={item.i18nKey}>
            <NavRow item={item} pathname={pathname} t={t} onNavigate={onNavigate} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function NavRow({
  item,
  pathname,
  t,
  onNavigate,
}: {
  item: NavItem;
  pathname: string;
  t: (key: string) => string;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  const isSoon = item.status === 'soon';
  const active = item.href
    ? pathname === item.href || pathname.startsWith(`${item.href}/`)
    : false;

  // Active state uses a left-edge accent + tinted text — same family
  // as Linear / Vercel sidebars where the active row is the obvious
  // anchor but doesn't shout.
  const rowClass = cn(
    'group flex items-center gap-2 px-2 py-1.5 text-[13px] transition-colors',
    isSoon
      ? 'cursor-not-allowed text-muted-foreground/60'
      : active
        ? 'bg-primary/10 font-medium text-primary'
        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
  );

  const content = (
    <>
      <Icon className={cn('h-3.5 w-3.5 shrink-0', isSoon && 'opacity-60')} />
      <span className="flex-1 truncate">{t(item.i18nKey)}</span>
      {item.status && item.status !== 'active' && (
        <StatusBadge variant={item.status}>
          {t(`status.${item.status}`)}
        </StatusBadge>
      )}
    </>
  );

  if (isSoon || !item.href) {
    return <div className={rowClass}>{content}</div>;
  }
  return (
    <Link href={item.href} onClick={onNavigate} className={rowClass}>
      {content}
    </Link>
  );
}
