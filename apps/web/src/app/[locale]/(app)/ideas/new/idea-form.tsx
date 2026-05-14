'use client';

import { Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import type { IdeaCreate } from '@bluedev/shared-types';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { useCreateIdea, useMatchIdea } from '@/lib/api/queries';

/** Split a comma-separated string into a trimmed, de-duped list. */
function splitCsv(value: string): string[] {
  return [
    ...new Set(
      value
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    ),
  ];
}

function toNumber(value: string): number | undefined {
  if (value.trim() === '') return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

export function IdeaForm() {
  const t = useTranslations('ideas.new');
  const router = useRouter();
  const { toast } = useToast();
  const createIdea = useCreateIdea();
  const matchIdea = useMatchIdea();

  const [title, setTitle] = useState('');
  const [abstract, setAbstract] = useState('');
  const [technologyAngle, setTechnologyAngle] = useState('');
  const [targetMarket, setTargetMarket] = useState('');
  const [trl, setTrl] = useState('');
  const [budgetMin, setBudgetMin] = useState('');
  const [budgetMax, setBudgetMax] = useState('');
  const [sectors, setSectors] = useState('');
  const [keywords, setKeywords] = useState('');

  // create → match → redirect is one logical action for the user; both
  // mutations' pending states fold into a single button spinner.
  const busy = createIdea.isPending || matchIdea.isPending;

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const payload: IdeaCreate = {
      title: title.trim(),
      abstract: abstract.trim(),
      technology_angle: technologyAngle.trim() || undefined,
      target_market: targetMarket.trim() || undefined,
      trl_estimate: toNumber(trl),
      budget_estimate_eur_min: toNumber(budgetMin),
      budget_estimate_eur_max: toNumber(budgetMax),
      sectors: splitCsv(sectors),
      keywords: splitCsv(keywords),
    };

    try {
      const idea = await createIdea.mutateAsync(payload);
      // Kick off the matcher immediately — the user came here to see
      // matches, not to land on an empty idea page. Match failure is
      // non-fatal: we still route to the matches page, which shows the
      // "run matcher" affordance.
      try {
        await matchIdea.mutateAsync({ ideaId: idea.id });
      } catch {
        // swallow — matches page handles the empty/re-run state
      }
      router.push(`/ideas/${idea.id}/matches`);
    } catch (err) {
      toast({
        title: 'Error',
        description: err instanceof Error ? err.message : 'Could not save idea',
        variant: 'destructive',
      });
    }
  }

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t('title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-1.5">
              <Label htmlFor="idea-title">{t('titleLabel')}</Label>
              <Input
                id="idea-title"
                data-testid="idea-title"
                required
                minLength={3}
                maxLength={300}
                placeholder={t('titlePlaceholder')}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="idea-abstract">{t('abstractLabel')}</Label>
              <Textarea
                id="idea-abstract"
                data-testid="idea-abstract"
                required
                minLength={20}
                maxLength={8000}
                rows={8}
                placeholder={t('abstractPlaceholder')}
                value={abstract}
                onChange={(e) => setAbstract(e.target.value)}
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="idea-tech">{t('technologyAngleLabel')}</Label>
                <Input
                  id="idea-tech"
                  maxLength={2000}
                  value={technologyAngle}
                  onChange={(e) => setTechnologyAngle(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="idea-market">{t('targetMarketLabel')}</Label>
                <Input
                  id="idea-market"
                  maxLength={2000}
                  value={targetMarket}
                  onChange={(e) => setTargetMarket(e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="idea-trl">{t('trlLabel')}</Label>
                <Input
                  id="idea-trl"
                  type="number"
                  min={1}
                  max={9}
                  value={trl}
                  onChange={(e) => setTrl(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="idea-budget-min">{t('budgetMinLabel')}</Label>
                <Input
                  id="idea-budget-min"
                  type="number"
                  min={0}
                  value={budgetMin}
                  onChange={(e) => setBudgetMin(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="idea-budget-max">{t('budgetMaxLabel')}</Label>
                <Input
                  id="idea-budget-max"
                  type="number"
                  min={0}
                  value={budgetMax}
                  onChange={(e) => setBudgetMax(e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="idea-sectors">{t('sectorsLabel')}</Label>
                <Input
                  id="idea-sectors"
                  value={sectors}
                  onChange={(e) => setSectors(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="idea-keywords">{t('keywordsLabel')}</Label>
                <Input
                  id="idea-keywords"
                  value={keywords}
                  onChange={(e) => setKeywords(e.target.value)}
                />
              </div>
            </div>

            <Button
              type="submit"
              data-testid="idea-submit"
              disabled={busy || title.trim().length < 3 || abstract.trim().length < 20}
              className="w-full"
            >
              {busy ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {createIdea.isPending ? t('submitting') : t('submit')}
                </>
              ) : (
                t('submit')
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
