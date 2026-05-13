import { ArrowRight, CheckCircle2, FileText, Globe, Shield, Sparkles, Zap } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { setRequestLocale } from 'next-intl/server';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <HomeContent />;
}

function HomeContent() {
  const t = useTranslations('marketing');

  return (
    <div className="min-h-screen bg-background">
      {/* ── Navbar ─────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 border-b bg-background/80 backdrop-blur-lg">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <FileText className="h-4 w-4" />
            </div>
            <span className="text-lg font-bold tracking-tight">Bluedev GrantWriter</span>
          </div>
          <nav className="hidden items-center gap-6 md:flex">
            <Link href="/home#features" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
              {t('nav.features')}
            </Link>
            <Link href="/pricing" className="text-sm text-muted-foreground transition-colors hover:text-foreground">
              {t('nav.pricing')}
            </Link>
          </nav>
          <div className="flex items-center gap-3">
            <Link href="/login">
              <Button variant="ghost" size="sm">{t('nav.login')}</Button>
            </Link>
            <Link href="/signup">
              <Button size="sm">
                {t('nav.signup')}
                <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
              </Button>
            </Link>
          </div>
        </div>
      </header>

      {/* ── Hero ───────────────────────────────────────────────────── */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 -z-10 bg-gradient-to-b from-primary/5 via-transparent to-transparent" />
        <div className="mx-auto max-w-6xl px-4 py-24 text-center sm:px-6 sm:py-32 lg:py-40">
          <div className="mx-auto mb-6 inline-flex items-center gap-2 rounded-full border bg-muted/60 px-4 py-1.5 text-sm font-medium text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5 text-primary" />
            {t('hero.badge')}
          </div>
          <h1 className="mx-auto max-w-3xl text-4xl font-extrabold tracking-tight text-foreground sm:text-5xl lg:text-6xl">
            {t('hero.title')}
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg text-muted-foreground sm:text-xl">
            {t('hero.subtitle')}
          </p>
          <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row sm:justify-center">
            <Link href="/signup">
              <Button size="lg" className="min-w-[200px] text-base">
                {t('hero.cta')}
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
            <Link href="/home#features">
              <Button variant="outline" size="lg" className="min-w-[200px] text-base">
                {t('hero.secondaryCta')}
              </Button>
            </Link>
          </div>
          <p className="mt-4 text-sm text-muted-foreground">{t('hero.note')}</p>
        </div>
      </section>

      {/* ── Stats ──────────────────────────────────────────────────── */}
      <section className="border-y bg-muted/30">
        <div className="mx-auto grid max-w-6xl grid-cols-2 gap-6 px-4 py-12 sm:px-6 lg:grid-cols-4">
          {(['programs', 'agents', 'draftTime', 'languages'] as const).map((key) => (
            <div key={key} className="text-center">
              <div className="text-3xl font-bold text-primary">{t(`stats.${key}.value`)}</div>
              <div className="mt-1 text-sm text-muted-foreground">{t(`stats.${key}.label`)}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Features ───────────────────────────────────────────────── */}
      <section id="features" className="scroll-mt-20">
        <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
          <div className="text-center">
            <h2 className="text-3xl font-bold tracking-tight">{t('features.title')}</h2>
            <p className="mx-auto mt-3 max-w-2xl text-muted-foreground">{t('features.subtitle')}</p>
          </div>
          <div className="mt-14 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <FeatureCard
              icon={<Zap className="h-5 w-5" />}
              title={t('features.aiDraft.title')}
              description={t('features.aiDraft.description')}
            />
            <FeatureCard
              icon={<CheckCircle2 className="h-5 w-5" />}
              title={t('features.citations.title')}
              description={t('features.citations.description')}
            />
            <FeatureCard
              icon={<Shield className="h-5 w-5" />}
              title={t('features.compliance.title')}
              description={t('features.compliance.description')}
            />
            <FeatureCard
              icon={<Globe className="h-5 w-5" />}
              title={t('features.bilingual.title')}
              description={t('features.bilingual.description')}
            />
            <FeatureCard
              icon={<FileText className="h-5 w-5" />}
              title={t('features.export.title')}
              description={t('features.export.description')}
            />
            <FeatureCard
              icon={<Sparkles className="h-5 w-5" />}
              title={t('features.distinctiveness.title')}
              description={t('features.distinctiveness.description')}
            />
          </div>
        </div>
      </section>

      {/* ── Supported Programs ─────────────────────────────────────── */}
      <section className="border-t bg-muted/20">
        <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
          <h2 className="text-center text-3xl font-bold tracking-tight">{t('programs.title')}</h2>
          <p className="mx-auto mt-3 max-w-2xl text-center text-muted-foreground">{t('programs.subtitle')}</p>
          <div className="mx-auto mt-10 grid max-w-3xl gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {['tubitak1501', 'tubitak1507', 'kosgeb', 'horizonEU', 'cascade', 'nlnet'].map((key) => (
              <div
                key={key}
                className="flex items-center gap-3 rounded-lg border bg-card p-4 transition-shadow hover:shadow-md"
              >
                <CheckCircle2 className="h-5 w-5 shrink-0 text-primary" />
                <span className="text-sm font-medium">{t(`programs.list.${key}`)}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ────────────────────────────────────────────────────── */}
      <section className="border-t">
        <div className="mx-auto max-w-6xl px-4 py-20 text-center sm:px-6">
          <h2 className="text-3xl font-bold tracking-tight">{t('cta.title')}</h2>
          <p className="mx-auto mt-3 max-w-lg text-muted-foreground">{t('cta.subtitle')}</p>
          <div className="mt-8">
            <Link href="/signup">
              <Button size="lg" className="text-base">
                {t('cta.button')}
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
          </div>
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────── */}
      <footer className="border-t bg-muted/30">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 px-4 py-8 sm:flex-row sm:justify-between sm:px-6">
          <p className="text-sm text-muted-foreground">© {new Date().getFullYear()} Bluedev. {t('footer.rights')}</p>
          <div className="flex gap-6 text-sm text-muted-foreground">
            <Link href="/login" className="hover:text-foreground">{t('nav.login')}</Link>
            <Link href="/pricing" className="hover:text-foreground">{t('nav.pricing')}</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

function FeatureCard({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
  return (
    <Card className="border bg-card/50 transition-all hover:border-primary/30 hover:shadow-lg hover:shadow-primary/5">
      <CardHeader className="pb-3">
        <div className="mb-2 flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
          {icon}
        </div>
        <CardTitle className="text-lg">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <CardDescription className="text-sm leading-relaxed">{description}</CardDescription>
      </CardContent>
    </Card>
  );
}
