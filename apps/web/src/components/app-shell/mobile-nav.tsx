'use client';

import { FileText, Menu, Settings } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';

import { PRIMARY_NAV, SETTINGS_NAV, SidebarSection } from '@/components/app-shell/sidebar';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { usePathname } from '@/i18n/navigation';

/**
 * Hamburger drawer for narrow viewports. Reuses the desktop sidebar's
 * nav item arrays + section renderer, so the only thing this component
 * owns is the open/close state + the trigger button. Closes itself when
 * the user picks an item (via the `onNavigate` callback) so the next
 * page render isn't covered by the dialog.
 *
 * Visible only `md:hidden` — the topbar mounts both this and the
 * locale switcher + user menu unconditionally; Tailwind hides the wrong
 * one for the active breakpoint.
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
          aria-label={t('settings')}
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
            Bluedev
          </DialogTitle>
        </DialogHeader>
        <nav className="flex flex-col gap-1 p-3">
          <SidebarSection
            items={PRIMARY_NAV}
            pathname={pathname}
            isAdmin={isAdmin}
            t={t}
            onNavigate={() => setOpen(false)}
          />
          <div className="my-2 flex items-center gap-2 px-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            <Settings className="h-3 w-3" />
            {t('settings')}
          </div>
          <SidebarSection
            items={SETTINGS_NAV}
            pathname={pathname}
            isAdmin={isAdmin}
            t={t}
            onNavigate={() => setOpen(false)}
          />
        </nav>
      </DialogContent>
    </Dialog>
  );
}
