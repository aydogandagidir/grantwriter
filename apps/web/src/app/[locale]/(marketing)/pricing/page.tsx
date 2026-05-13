import { ArrowRight, Check, FileText } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { setRequestLocale } from 'next-intl/server';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';
import { cn } from '@/lib/utils';

export default async function PricingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <PricingContent />;
}

function PricingContent() {
  const t = useTranslations('pricing');

  const plans = [
    {
      key: 'starter',
      price: '$0',
      period: t('perMonth'),
      featured: false,
      features: [
        t('features.proposals3'),
        t('features.programs5'),
        t('features.citations'),
        t('features.docxExport'),
        t('features.communitySupport'),
      ],
    },
    {
      key: 'pro',
      price: '$49',
      period: t('perMonth'),
      featured: true,
      features: [
        t('features.proposalsUnlimited'),
        t('features.programs5'),
        t('features.citations'),
        t('features.docxExport'),
        t('features.xlsxExport'),
        t('features.byok'),
        t('features.prioritySupport'),
        t('features.teamMembers'),
      ],
    },
    {
      key: 'enterprise',
      price: t('contactUs'),
      period: '',
      featured: false,
      features: [
        t('features.proposalsUnlimited'),
        t('features.programs5'),
        t('features.citations'),
        t('features.allExports'),
        t('features.byok'),
        t('features.dedicatedSupport'),
        t('features.sla'),
        t('features.customIntegrations'),
        t('features.selfHosted'),
      ],
    },
  ];

  return (
    <div className="min-h-screen bg-background">
      {/* ── Navbar (same as home) ──────────────────────────────────── */}
      <header className="sticky top-0 z-50 border-b bg-background/80 backdrop-blur-lg">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Link href="/home" className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <FileText className="h-4 w-4" />
            </div>
            <span className="text-lg font-bold tracking-tight">Bluedev GrantWriter</span>
          </Link>
          <div className="flex items-center gap-3">
            <Link href="/login">
              <Button variant="ghost" size="sm">{t('login')}</Button>
            </Link>
            <Link href="/signup">
              <Button size="sm">
                {t('signup')}
                <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
              </Button>
            </Link>
          </div>
        </div>
      </header>

      {/* ── Pricing Header ─────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 -z-10 bg-gradient-to-b from-primary/5 via-transparent to-transparent" />
        <div className="mx-auto max-w-6xl px-4 py-20 text-center sm:px-6">
          <h1 className="text-4xl font-extrabold tracking-tight sm:text-5xl">{t('title')}</h1>
          <p className="mx-auto mt-4 max-w-2xl text-lg text-muted-foreground">{t('subtitle')}</p>
        </div>
      </section>

      {/* ── Pricing Cards ──────────────────────────────────────────── */}
      <section className="-mt-4 pb-20">
        <div className="mx-auto grid max-w-5xl gap-6 px-4 sm:px-6 lg:grid-cols-3">
          {plans.map((plan) => (
            <Card
              key={plan.key}
              className={cn(
                'relative flex flex-col transition-all',
                plan.featured
                  ? 'border-primary shadow-xl shadow-primary/10 ring-1 ring-primary'
                  : 'hover:shadow-lg',
              )}
            >
              {plan.featured && (
                <div className="absolute -top-3.5 left-1/2 -translate-x-1/2 rounded-full bg-primary px-4 py-1 text-xs font-semibold text-primary-foreground">
                  {t('popular')}
                </div>
              )}
              <CardHeader className="pb-2 pt-8 text-center">
                <CardTitle className="text-xl">{t(`plans.${plan.key}.name`)}</CardTitle>
                <CardDescription className="mt-1">{t(`plans.${plan.key}.description`)}</CardDescription>
                <div className="mt-4">
                  <span className="text-4xl font-extrabold">{plan.price}</span>
                  {plan.period && (
                    <span className="ml-1 text-muted-foreground">/{plan.period}</span>
                  )}
                </div>
              </CardHeader>
              <CardContent className="flex flex-1 flex-col gap-6 pt-6">
                <ul className="flex-1 space-y-3">
                  {plan.features.map((feature, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                      <span>{feature}</span>
                    </li>
                  ))}
                </ul>
                <Link href="/signup" className="block">
                  <Button
                    className="w-full"
                    variant={plan.featured ? 'default' : 'outline'}
                    size="lg"
                  >
                    {plan.key === 'enterprise' ? t('contactSales') : t('getStarted')}
                  </Button>
                </Link>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────── */}
      <footer className="border-t bg-muted/30">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 px-4 py-8 sm:flex-row sm:justify-between sm:px-6">
          <p className="text-sm text-muted-foreground">© {new Date().getFullYear()} Bluedev. {t('rights')}</p>
          <div className="flex gap-6 text-sm text-muted-foreground">
            <Link href="/home" className="hover:text-foreground">{t('home')}</Link>
            <Link href="/login" className="hover:text-foreground">{t('login')}</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
