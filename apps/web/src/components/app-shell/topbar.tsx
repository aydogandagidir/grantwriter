'use client';

import { LogOut, User } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { LocaleSwitcher } from '@/components/app-shell/locale-switcher';
import { MobileNav } from '@/components/app-shell/mobile-nav';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { logoutAction } from '@/lib/auth/actions';

/**
 * App shell top bar.
 *
 * Anatomy:
 *
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ [≡] [WORKSPACE · Akasya] [PLAN · Starter]   [Dil] [avatar ▾]    │
 *   └─────────────────────────────────────────────────────────────────┘
 *
 * The left context chips ("WORKSPACE · X" + plan badge) anchor the
 * user — the sidebar shows where they are, the topbar shows what
 * they're working under. The plan badge doubles as a soft upsell
 * surface ("Pro'ya yükselt" link could live here in a follow-up).
 */
export function Topbar({
  email,
  displayName,
  tenantName,
  role,
  plan,
}: {
  email: string | null;
  displayName: string | null;
  tenantName: string;
  role: 'owner' | 'admin' | 'member' | 'viewer';
  plan: string;
}) {
  const tAuth = useTranslations('auth');
  const tNav = useTranslations('nav');
  const initials = (displayName ?? email ?? '?')
    .split(/[ @]/)
    .filter(Boolean)
    .map((p) => p[0]?.toUpperCase())
    .slice(0, 2)
    .join('');

  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b bg-card px-4 md:px-6">
      <div className="flex items-center gap-3">
        <MobileNav role={role} />
        <div className="hidden items-center gap-2 md:flex">
          <span className="rounded-md border border-primary/20 bg-primary/5 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-primary">
            {tNav('workspace')} · {tenantName}
          </span>
          <span className="rounded-md border bg-muted/40 px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            {tNav('plan')} · {plan}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <LocaleSwitcher />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="gap-2 px-2">
              <Avatar className="h-7 w-7">
                <AvatarFallback className="text-xs">
                  {initials || <User className="h-3 w-3" />}
                </AvatarFallback>
              </Avatar>
              <span className="hidden text-sm md:inline">{displayName ?? email}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col space-y-1">
                <span className="text-sm font-medium">{displayName ?? email}</span>
                {displayName && email && (
                  <span className="text-xs text-muted-foreground">{email}</span>
                )}
                <span className="pt-1 text-[11px] uppercase tracking-wider text-muted-foreground">
                  {tNav('roleLabel')}: {tNav(`roles.${role}`)}
                </span>
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <form action={logoutAction}>
              <DropdownMenuItem asChild>
                <button type="submit" className="w-full cursor-pointer">
                  <LogOut className="h-4 w-4" />
                  {tAuth('logout')}
                </button>
              </DropdownMenuItem>
            </form>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
