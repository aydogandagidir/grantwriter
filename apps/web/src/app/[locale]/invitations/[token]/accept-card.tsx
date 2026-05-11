'use client';

import { Loader2 } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';

import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/use-toast';
import { useRouter } from '@/i18n/navigation';
import { useAcceptInvitation } from '@/lib/api/queries';
import type { InvitationPreview } from '@bluedev/shared-types';

export function AcceptInvitationCard({
  token,
  preview,
}: {
  token: string;
  preview: InvitationPreview;
}) {
  const t = useTranslations('invitationAccept');
  const tCommon = useTranslations('common');
  const format = useFormatter();
  const { toast } = useToast();
  const router = useRouter();
  const accept = useAcceptInvitation();

  async function onAccept() {
    try {
      await accept.mutateAsync(token);
      toast({ title: t('successToast', { tenant: preview.tenant_name }) });
      router.push('/dashboard');
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <dl className="grid gap-2 text-sm">
        <Row label="Tenant" value={preview.tenant_name} />
        <Row label={t('invitedBy', { name: preview.inviter_display_name ?? '—' })} value="" />
        <Row
          label={t('expiresAt', { date: format.dateTime(new Date(preview.expires_at), 'short') })}
          value=""
        />
      </dl>
      <Button className="w-full" onClick={onAccept} disabled={accept.isPending}>
        {accept.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
        {t('accept')}
      </Button>
      <p className="text-center text-xs text-muted-foreground">
        {tCommon('loading')} → /dashboard
      </p>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground">{label}</span>
      {value && <span className="font-medium">{value}</span>}
    </div>
  );
}
