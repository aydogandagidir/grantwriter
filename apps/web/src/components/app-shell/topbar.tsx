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
 * Minimal app top bar.
 *
 *   ┌─────────────────────────────────────────────────────────────┐
 *   │ [≡]                                       [TR ▾]  [avatar ▾]│
 *   └─────────────────────────────────────────────────────────────┘
 *
 * Workspace + plan context lives in the sidebar (which is now sticky),
 * so the topbar is reduced to the controls that only make sense at the
 * top: locale switcher and the avatar / sign-out menu. The mobile
 * trigger sits on the left so the sidebar drawer is reachable when the
 * desktop sidebar is hidden.
 *
 * Sticky too, so it stays visible while the main column scrolls.
 */
export function Topbar({
  email,
  displayName,
  role,
}: {
  email: string | null;
  displayName: string | null;
  role: 'owner' | 'admin' | 'member' | 'viewer';
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
    <header className="sticky top-0 z-10 flex h-12 items-center justify-between gap-4 border-b bg-card px-4 md:px-6">
      <div className="flex items-center gap-2">
        <MobileNav role={role} />
      </div>
      <div className="flex items-center gap-2">
        <LocaleSwitcher />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="h-8 gap-2 px-2">
              <Avatar className="h-6 w-6">
                <AvatarFallback className="text-[10px] font-semibold">
                  {initials || <User className="h-3 w-3" />}
                </AvatarFallback>
              </Avatar>
              <span className="hidden text-sm md:inline">
                {displayName ?? email}
              </span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col space-y-1">
                <span className="text-sm font-medium">{displayName ?? email}</span>
                {displayName && email && (
                  <span className="text-xs text-muted-foreground">{email}</span>
                )}
                <span className="pt-1 text-[10px] uppercase tracking-wider text-muted-foreground">
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
