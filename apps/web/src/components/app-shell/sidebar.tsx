'use client';

import {
  FileText,
  Home,
  KeyRound,
  Mail,
  Receipt,
  ScrollText,
  Settings,
  TrendingUp,
  Users,
} from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { ComponentType } from 'react';

import { Link, usePathname } from '@/i18n/navigation';
import { cn } from '@/lib/utils';

export interface NavItem {
  href: string;
  i18nKey: string;
  icon: ComponentType<{ className?: string }>;
  adminOnly?: boolean;
}

export const PRIMARY_NAV: NavItem[] = [
  { href: '/dashboard', i18nKey: 'dashboard', icon: Home },
  { href: '/proposals', i18nKey: 'proposals', icon: FileText },
];

export const SETTINGS_NAV: NavItem[] = [
  { href: '/settings/llm-config', i18nKey: 'llmConfig', icon: KeyRound },
  { href: '/settings/members', i18nKey: 'members', icon: Users, adminOnly: true },
  { href: '/settings/invitations', i18nKey: 'invitations', icon: Mail, adminOnly: true },
  { href: '/settings/audit', i18nKey: 'audit', icon: ScrollText, adminOnly: true },
  { href: '/settings/usage', i18nKey: 'usage', icon: TrendingUp, adminOnly: true },
  { href: '/settings/billing', i18nKey: 'billing', icon: Receipt, adminOnly: true },
];

export function Sidebar({ role }: { role: 'owner' | 'admin' | 'member' | 'viewer' }) {
  const t = useTranslations('nav');
  const pathname = usePathname();
  const isAdmin = role === 'owner' || role === 'admin';

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r bg-card/40 md:flex">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <FileText className="h-4 w-4" />
        </div>
        <span className="text-sm font-semibold">Bluedev</span>
      </div>
      <nav className="flex flex-1 flex-col gap-1 p-3">
        <SidebarSection items={PRIMARY_NAV} pathname={pathname} isAdmin={isAdmin} t={t} />
        <div className="my-2 flex items-center gap-2 px-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          <Settings className="h-3 w-3" />
          {t('settings')}
        </div>
        <SidebarSection items={SETTINGS_NAV} pathname={pathname} isAdmin={isAdmin} t={t} />
      </nav>
    </aside>
  );
}

export function SidebarSection({
  items,
  pathname,
  isAdmin,
  t,
  onNavigate,
}: {
  items: NavItem[];
  pathname: string;
  isAdmin: boolean;
  t: (key: string) => string;
  onNavigate?: () => void;
}) {
  return (
    <>
      {items.map((item) => {
        if (item.adminOnly && !isAdmin) return null;
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              'flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors',
              active
                ? 'bg-primary/10 text-primary'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            <Icon className="h-4 w-4" />
            {t(item.i18nKey)}
          </Link>
        );
      })}
    </>
  );
}
