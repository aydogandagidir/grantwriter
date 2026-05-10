'use client';

import { CreditCard, ExternalLink, Loader2, XCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import { useBilling, useCancelSubscription, useCheckout } from '@/lib/api/queries';

const PRO_PLAN_REF = 'iyz_pro_monthly';

export function BillingPanel() {
  const t = useTranslations('billing');
  const tCommon = useTranslations('common');
  const { toast } = useToast();
  const { data, isLoading } = useBilling();
  const checkout = useCheckout();
  const cancel = useCancelSubscription();
  const [cancelOpen, setCancelOpen] = useState(false);

  async function onUpgrade() {
    try {
      const result = await checkout.mutateAsync(PRO_PLAN_REF);
      // Bounce the browser to Iyzico's hosted payment form. Webhook will
      // flip the plan once payment lands.
      window.location.href = result.payment_page_url;
    } catch (err) {
      toast({
        variant: 'destructive',
        title: t('checkoutFailed', { message: (err as Error).message }),
      });
    }
  }

  async function onCancel() {
    try {
      await cancel.mutateAsync();
      toast({ title: t('cancel') });
      setCancelOpen(false);
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-xl">
            <CreditCard className="h-5 w-5" />
            {t('subscription')}
          </CardTitle>
          <CardDescription>
            {isLoading ? (
              <Skeleton className="h-4 w-32" />
            ) : data?.has_active_subscription ? (
              <Badge variant="default">{t('active')}</Badge>
            ) : (
              <Badge variant="secondary">{t('inactive')}</Badge>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            <Stat
              label={t('plan')}
              value={isLoading ? null : (data?.plan ?? '—').toUpperCase()}
            />
            <Stat
              label={t('monthlyLimit')}
              value={isLoading ? null : String(data?.monthly_proposal_limit ?? 0)}
            />
            <Stat
              label={t('usedThisMonth')}
              value={isLoading ? null : String(data?.proposals_this_month ?? 0)}
            />
          </div>
          <Separator />
          <div className="flex flex-wrap gap-2">
            <Button onClick={onUpgrade} disabled={checkout.isPending}>
              {checkout.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              <ExternalLink className="h-4 w-4" />
              {t('checkoutPro')}
            </Button>
            {data?.has_active_subscription && (
              <Button variant="outline" onClick={() => setCancelOpen(true)}>
                <XCircle className="h-4 w-4" />
                {t('cancel')}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Dialog open={cancelOpen} onOpenChange={setCancelOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('cancel')}</DialogTitle>
            <DialogDescription>{t('cancelConfirm')}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCancelOpen(false)}>
              {tCommon('cancel')}
            </Button>
            <Button variant="destructive" onClick={onCancel} disabled={cancel.isPending}>
              {cancel.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('cancel')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      {value === null ? (
        <Skeleton className="mt-1 h-6 w-20" />
      ) : (
        <p className="text-xl font-semibold">{value}</p>
      )}
    </div>
  );
}
