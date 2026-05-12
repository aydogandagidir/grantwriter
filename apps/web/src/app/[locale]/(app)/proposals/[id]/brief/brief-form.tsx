'use client';

import { CheckCircle2, Loader2, Save } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import { useProposal, useUpdateProposal } from '@/lib/api/queries';

interface BriefFields {
  // Index signature satisfies the API's `Record<string, unknown>` brief
  // typing while still keeping the four MVP keys explicit.
  [key: string]: string;
  summary: string;
  objectives: string;
  methodology: string;
  partners: string;
}

/**
 * Programme-agnostic minimal brief form (Sprint 4 MVP).
 *
 * The real brief schema lives in each programme module
 * (apps/api/src/programs/<programme>/brief_schema.py). Sprint 4 ships this
 * 4-field stub so the pilot user has SOMETHING to type into; a future
 * sprint replaces this with the DynamicBriefForm (RHF + Zod) once the
 * schema endpoint is shipped.
 */
export function BriefForm({ proposalId }: { proposalId: string }) {
  const t = useTranslations('brief');
  const { toast } = useToast();
  const router = useRouter();
  const { data: proposal, isLoading } = useProposal(proposalId);
  const update = useUpdateProposal(proposalId);

  const [fields, setFields] = useState<BriefFields>({
    summary: '',
    objectives: '',
    methodology: '',
    partners: '',
  });

  useEffect(() => {
    if (proposal) {
      const brief = proposal.brief as Partial<BriefFields>;
      setFields({
        summary: typeof brief.summary === 'string' ? brief.summary : '',
        objectives: typeof brief.objectives === 'string' ? brief.objectives : '',
        methodology: typeof brief.methodology === 'string' ? brief.methodology : '',
        partners: typeof brief.partners === 'string' ? brief.partners : '',
      });
    }
  }, [proposal]);

  if (isLoading || !proposal) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
          <CardDescription>{t('subtitle')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </CardContent>
      </Card>
    );
  }

  const save = async () => {
    try {
      await update.mutateAsync({ brief: fields });
      toast({ title: t('savedToast') });
    } catch {
      toast({ variant: 'destructive', title: t('errorGeneric') });
    }
  };

  const markReady = async () => {
    if (!window.confirm(t('markReadyConfirm'))) {
      return;
    }
    try {
      await update.mutateAsync({ brief: fields, status: 'brief_complete' });
      toast({ title: t('savedToast') });
      router.push(`/proposals/${proposalId}`);
    } catch {
      toast({ variant: 'destructive', title: t('errorGeneric') });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('subtitle')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="summary">{t('summary')}</Label>
          <Textarea
            id="summary"
            value={fields.summary}
            onChange={(e) => setFields({ ...fields, summary: e.target.value })}
            placeholder={t('summaryPlaceholder')}
            rows={4}
            data-testid="brief-summary"
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="objectives">{t('objectives')}</Label>
          <Textarea
            id="objectives"
            value={fields.objectives}
            onChange={(e) =>
              setFields({ ...fields, objectives: e.target.value })
            }
            placeholder={t('objectivesPlaceholder')}
            rows={4}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="methodology">{t('methodology')}</Label>
          <Textarea
            id="methodology"
            value={fields.methodology}
            onChange={(e) =>
              setFields({ ...fields, methodology: e.target.value })
            }
            placeholder={t('methodologyPlaceholder')}
            rows={4}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="partners">{t('partners')}</Label>
          <Textarea
            id="partners"
            value={fields.partners}
            onChange={(e) => setFields({ ...fields, partners: e.target.value })}
            placeholder={t('partnersPlaceholder')}
            rows={3}
          />
        </div>
        <div className="flex flex-wrap gap-3 pt-2">
          <Button
            variant="outline"
            onClick={save}
            disabled={update.isPending}
            className="gap-2"
            data-testid="brief-save"
          >
            {update.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                {t('saving')}
              </>
            ) : (
              <>
                <Save className="h-4 w-4" aria-hidden="true" />
                {t('save')}
              </>
            )}
          </Button>
          <Button
            onClick={markReady}
            disabled={update.isPending}
            className="gap-2"
          >
            <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
            {t('markReady')}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
