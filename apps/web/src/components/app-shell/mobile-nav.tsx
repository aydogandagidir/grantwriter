'use client';

import { FileText, Menu } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';

import { NAV_SECTIONS, type NavItem, type NavSection } from '@/components/app-shell/sidebar';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { StatusBadge } from '@/components/ui/status-badge';
import { Link, usePathname } from '@/i18n/navigation';
import { cn } from '@/lib/utils';

/**
 * Hamburger drawer for narrow viewports. Mirrors the desktop sidebar's
 * sectioned nav and StatusBadge treatment so the two surfaces stay in
 * lockstep without sharing a component (the desktop sidebar uses a
 * larger footer block we don't have room for here).
 *
 * Visible only ``md:hidden``; the topbar mounts both this trigger and
 * the locale + user menu, and Tailwind hides whichever one doesn't fit
 * the active breakpoint.
 */
export function MobileNav({
  role,
}: {
  role: 'owner' | 'admin' | 'member' | 'viewer';
}) {
  const t = useTranslations('nav');
  const pathname = usePathname();
  const isAdmin = role === 'owner' || role === 'admin';
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden"
          aria-label={t('openMenu')}
        >
          <Menu className="h-5 w-5" />
        </Button>
      </DialogTrigger>
      <DialogContent className="left-0 top-0 h-full max-w-[280px] translate-x-0 translate-y-0 rounded-none border-r p-0">
        <DialogHeader className="border-b p-4">
          <DialogTitle className="flex items-center gap-2 text-base">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <FileText className="h-4 w-4" />
            </div>
            <span className="font-semibold tracking-tight">bluedev</span>
            <span className="rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              GrantWriter
            </span>
          </DialogTitle>
        </DialogHeader>
        <nav className="space-y-5 overflow-y-auto px-3 py-4">
          {NAV_SECTIONS.map((section) => (
            <MobileSection
              key={section.i18nKey}
              section={section}
              pathname={pathname}
              isAdmin={isAdmin}
              t={t}
              onNavigate={() => setOpen(false)}
            />
          ))}
        </nav>
      </DialogContent>
    </Dialog>
  );
}

function MobileSection({
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
  onNavigate: () => void;
}) {
  const visible = section.items.filter((item) => !item.adminOnly || isAdmin);
  if (visible.length === 0) return null;

  return (
    <div>
      <div className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t(section.i18nKey)}
      </div>
      <ul className="space-y-0.5">
        {visible.map((item) => (
          <li key={item.i18nKey}>
            <MobileRow
              item={item}
              pathname={pathname}
              t={t}
              onNavigate={onNavigate}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

function MobileRow({
  item,
  pathname,
  t,
  onNavigate,
}: {
  item: NavItem;
  pathname: string;
  t: (key: string) => string;
  onNavigate: () => void;
}) {
  const Icon = item.icon;
  const isSoon = item.status === 'soon';
  const active = item.href
    ? pathname === item.href || pathname.startsWith(`${item.href}/`)
    : false;

  const rowClass = cn(
    'flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors',
    isSoon
      ? 'cursor-not-allowed text-muted-foreground/70'
      : active
        ? 'bg-primary/10 font-medium text-primary'
        : 'text-muted-foreground hover:bg-muted hover:text-foreground',
  );

  const inner = (
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
    return <div className={rowClass}>{inner}</div>;
  }
  return (
    <Link href={item.href} onClick={onNavigate} className={rowClass}>
      {inner}
    </Link>
  );
}
