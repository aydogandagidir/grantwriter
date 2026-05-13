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
  Settings,
  Sparkles,
  TrendingUp,
  Users,
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { ComponentType } from 'react';

import { Link, usePathname } from '@/i18n/navigation';
import { cn } from '@/lib/utils';

import { StatusBadge, type StatusBadgeVariant } from '@/components/ui/status-badge';

/**
 * App-shell sidebar — sectioned navigation with availability badges.
 *
 * Layout:
 *
 *   ┌────────────────────────┐
 *   │ [logo] bluedev  [tag]  │  brand header
 *   │ <tenant name>          │
 *   ├────────────────────────┤
 *   │ ÖZET                   │  section header (caps)
 *   │ ▸ Modül Merkezi        │  active row (primary tint)
 *   │                        │
 *   │ TEKLİFLER              │
 *   │ ▸ Tüm Teklifler [AKTİF]│
 *   │ + Yeni Teklif          │
 *   │                        │
 *   │ PROGRAMLAR             │
 *   │ ▸ TÜBİTAK 1501 [AKTİF] │
 *   │ ▸ Horizon EU   [BETA]  │
 *   │ ▸ ...                  │
 *   │                        │
 *   │ ARAÇLAR / YÖNETİM ...  │
 *   ├────────────────────────┤
 *   │ user@example.com       │  footer
 *   │ Rol: owner             │
 *   │ Settings · Logout      │
 *   └────────────────────────┘
 *
 * Items with ``adminOnly`` are filtered out for ``member`` / ``viewer``
 * roles. Items with ``status: 'soon'`` are rendered muted and
 * non-interactive — they exist for discoverability so users see what's
 * coming without thinking the app is broken when nothing happens.
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
      { href: '/proposals', i18nKey: 'proposals', icon: FileText, status: 'active' },
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
    <aside className="hidden w-64 shrink-0 flex-col border-r bg-card md:flex">
      {/* Brand + tenant header */}
      <div className="border-b px-4 py-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-sm shadow-primary/30">
            <FileText className="h-4 w-4" />
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-sm font-semibold tracking-tight">bluedev</span>
            <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              GrantWriter
            </span>
          </div>
        </div>
        <p className="mt-2.5 truncate text-sm text-foreground" title={tenantName}>
          {tenantName}
        </p>
      </div>

      {/* Sectioned nav */}
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
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

      {/* Footer */}
      <div className="border-t px-4 py-3">
        <p className="truncate text-xs text-muted-foreground" title={email ?? ''}>
          {email}
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          {t('roleLabel')}: {t(`roles.${role}`)}
        </p>
        <div className="mt-2 flex gap-3 text-[11px] text-muted-foreground">
          <Link
            href="/settings/llm-config"
            className="flex items-center gap-1 hover:text-foreground"
          >
            <Settings className="h-3 w-3" />
            {t('settings')}
          </Link>
        </div>
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
      <div className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t(section.i18nKey)}
      </div>
      <ul className="space-y-0.5">
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

  const rowClass = cn(
    'group flex items-center gap-2.5 rounded-md px-3 py-1.5 text-sm transition-colors',
    isSoon
      ? 'cursor-not-allowed text-muted-foreground/70'
      : active
        ? 'bg-primary/10 font-medium text-primary'
        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
  );

  const content = (
    <>
      <Icon className={cn('h-4 w-4 shrink-0', isSoon && 'opacity-60')} />
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
