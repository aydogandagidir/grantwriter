'use client';

import { Loader2, PlusCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useToast } from '@/components/ui/use-toast';
import { useCreateProposal, useProgrammes } from '@/lib/api/queries';

/**
 * Sprint 4 MVP — "New proposal" minimal form. Replaces the
 * `?new=1` query placeholder the dashboard "New proposal" button
 * pointed at. On submit we POST `/api/v1/proposals` and route to the
 * brief page (`/proposals/[id]/brief`) so the user can fill in the
 * programme-specific brief immediately.
 */
export function NewProposalForm({ locale }: { locale: string }) {
  const t = useTranslations('newProposal');
  const { toast } = useToast();
  const router = useRouter();
  const programmes = useProgrammes();
  const create = useCreateProposal();

  const [programmeId, setProgrammeId] = useState<string>('');
  const [language, setLanguage] = useState<'tr' | 'en'>(
    locale === 'tr' ? 'tr' : 'en',
  );
  const [title, setTitle] = useState('');

  const isPending = create.isPending;
  const canSubmit = Boolean(programmeId) && !isPending;

  return (
    <Card className="mx-auto max-w-xl">
      <CardHeader>
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('subtitle')}</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!programmeId) {
              return;
            }
            try {
              const created = await create.mutateAsync({
                programme_id: programmeId,
                language,
                title: title.trim() || undefined,
              });
              router.push(`/proposals/${created.id}/brief`);
            } catch {
              toast({
                variant: 'destructive',
                title: t('errorGeneric'),
              });
            }
          }}
        >
          <div className="space-y-2">
            <Label htmlFor="programme">{t('programme')}</Label>
            <Select value={programmeId} onValueChange={setProgrammeId}>
              <SelectTrigger id="programme" data-testid="programme-select">
                <SelectValue placeholder={t('programmePlaceholder')} />
              </SelectTrigger>
              <SelectContent>
                {programmes.data?.programmes.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {locale === 'tr' ? p.name_tr : p.name_en}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="language">{t('language')}</Label>
            <Select
              value={language}
              onValueChange={(value) => setLanguage(value as 'tr' | 'en')}
            >
              <SelectTrigger id="language">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tr">{t('languageTr')}</SelectItem>
                <SelectItem value="en">{t('languageEn')}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="title">{t('titleField')}</Label>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t('titlePlaceholder')}
              data-testid="new-proposal-title"
            />
          </div>

          <Button
            type="submit"
            className="w-full gap-2"
            disabled={!canSubmit}
            data-testid="new-proposal-submit"
          >
            {isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                {t('submitting')}
              </>
            ) : (
              <>
                <PlusCircle className="h-4 w-4" aria-hidden="true" />
                {t('submit')}
              </>
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
