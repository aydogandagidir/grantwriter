'use client';

import { LogOut, User } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { LocaleSwitcher } from '@/components/app-shell/locale-switcher';
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

export function Topbar({
  email,
  displayName,
  tenantName,
}: {
  email: string | null;
  displayName: string | null;
  tenantName: string;
}) {
  const t = useTranslations('auth');
  const initials = (displayName ?? email ?? '?')
    .split(/[ @]/)
    .filter(Boolean)
    .map((p) => p[0]?.toUpperCase())
    .slice(0, 2)
    .join('');

  return (
    <header className="flex h-14 items-center justify-between gap-4 border-b bg-card/40 px-6">
      <div className="text-sm font-medium text-muted-foreground">{tenantName}</div>
      <div className="flex items-center gap-2">
        <LocaleSwitcher />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="gap-2 px-2">
              <Avatar className="h-7 w-7">
                <AvatarFallback className="text-xs">{initials || <User className="h-3 w-3" />}</AvatarFallback>
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
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <form action={logoutAction}>
              <DropdownMenuItem asChild>
                <button type="submit" className="w-full cursor-pointer">
                  <LogOut className="h-4 w-4" />
                  {t('logout')}
                </button>
              </DropdownMenuItem>
            </form>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
