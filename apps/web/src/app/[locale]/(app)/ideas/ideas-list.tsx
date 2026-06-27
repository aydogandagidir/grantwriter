'use client';

import { Lightbulb, PlusCircle } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Link } from '@/i18n/navigation';
import { useIdeas } from '@/lib/api/queries';

export function IdeasList() {
  const t = useTranslations('ideas');
  const format = useFormatter();
  const { data, isLoading } = useIdeas();

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t('listTitle')}</h1>
          <p className="text-muted-foreground">{t('listSubtitle')}</p>
        </div>
        <Button asChild>
          <Link href="/ideas/new">
            <PlusCircle className="h-4 w-4" />
            {t('newIdea')}
          </Link>
        </Button>
      </header>

      {isLoading ? (
        <div className="grid gap-3 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, idx) => (
            <Skeleton key={idx} className="h-32 w-full" />
          ))}
        </div>
      ) : (data?.ideas.length ?? 0) === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <Lightbulb className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">{t('empty')}</p>
            <Button asChild variant="outline">
              <Link href="/ideas/new">
                <PlusCircle className="h-4 w-4" />
                {t('newIdea')}
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2" data-testid="ideas-grid">
          {data!.ideas.map((idea) => (
            <Card key={idea.id} className="flex flex-col" data-testid="idea-card">
              <CardContent className="flex flex-1 flex-col gap-2 pt-6">
                <Link
                  href={`/ideas/${idea.id}/matches`}
                  className="font-semibold leading-snug hover:underline"
                >
                  {idea.title}
                </Link>
                <p className="line-clamp-2 text-sm text-muted-foreground">
                  {idea.abstract}
                </p>
                <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-2">
                  {idea.trl_estimate !== null ? (
                    <Badge variant="secondary" className="text-xs">
                      TRL {idea.trl_estimate}
                    </Badge>
                  ) : null}
                  {idea.sectors.slice(0, 2).map((s) => (
                    <Badge key={s} variant="secondary" className="text-xs font-normal">
                      {s}
                    </Badge>
                  ))}
                  <span className="ml-auto text-xs text-muted-foreground">
                    {format.relativeTime(new Date(idea.created_at))}
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
