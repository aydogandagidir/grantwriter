'use client';

/**
 * Two-step workspace bootstrap wizard.
 *
 * Step 1 collects the workspace name + optional slug + default
 * language; step 2 confirms the starter plan choice. The slug is
 * either user-typed or auto-derived inside the backend — the FE
 * doesn't try to replicate the server's slugify rules.
 *
 * On success the FE redirects to `/{locale}/dashboard`. Server-side
 * errors come back as `ApiError`; we map the canonical 409 + 400
 * cases to translated inline messages so the user can recover
 * without leaving the wizard.
 */

import { useRouter } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/components/ui/use-toast';
import { ApiError } from '@/lib/api/client';
import { useCreateWorkspace } from '@/lib/api/queries';

type WizardStep = 1 | 2;

export function OnboardingWizard() {
  const t = useTranslations('onboarding');
  const tCommon = useTranslations('common');
  const tErrors = useTranslations('errors');
  const router = useRouter();
  const locale = useLocale();
  const { toast } = useToast();
  const createWorkspace = useCreateWorkspace();

  const [step, setStep] = useState<WizardStep>(1);
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [language, setLanguage] = useState<'tr' | 'en'>(
    locale === 'tr' ? 'tr' : 'en',
  );
  const [inlineError, setInlineError] = useState<string | null>(null);

  // Step-1 → Step-2 guard: at least a 2-char name; the backend re-
  // validates so this is just to keep "next" disabled until typing.
  const canAdvance = name.trim().length >= 2;

  async function handleCreate(): Promise<void> {
    setInlineError(null);
    try {
      await createWorkspace.mutateAsync({
        name: name.trim(),
        slug: slug.trim() || undefined,
        preferred_language: language,
      });
      toast({ title: t('successToast') });
      router.replace(`/${locale}/dashboard`);
    } catch (err) {
      if (err instanceof ApiError) {
        // Map the two recoverable backend errors to translated copy.
        if (err.status === 409 && err.detail.includes('slug')) {
          setInlineError(t('errorSlugTaken'));
          setStep(1);
          return;
        }
        if (err.status === 400 && err.detail.includes('slug')) {
          setInlineError(t('errorInvalidSlug'));
          setStep(1);
          return;
        }
      }
      toast({
        variant: 'destructive',
        title: tCommon('error'),
        description: (err as Error).message ?? tErrors('generic'),
      });
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 px-4 py-12">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
          <CardDescription>{t('subtitle')}</CardDescription>
          <p className="pt-2 text-xs text-muted-foreground">
            {t('stepIndicator', { current: step, total: 2 })}
          </p>
        </CardHeader>
        <CardContent>
          {step === 1 ? (
            <Step1
              name={name}
              setName={setName}
              slug={slug}
              setSlug={setSlug}
              language={language}
              setLanguage={setLanguage}
              inlineError={inlineError}
              translations={{
                title: t('step1Title'),
                description: t('step1Description'),
                nameLabel: t('nameLabel'),
                namePlaceholder: t('namePlaceholder'),
                slugLabel: t('slugLabel'),
                slugHint: t('slugHint'),
                languageLabel: t('languageLabel'),
              }}
            />
          ) : (
            <Step2
              translations={{
                title: t('step2Title'),
                description: t('step2Description'),
                starterTitle: t('starterTitle'),
                starterPrice: t('starterPrice'),
                starterFeatures: t('starterFeatures'),
                starterRecommended: t('starterRecommended'),
              }}
            />
          )}

          <div className="mt-6 flex items-center justify-between">
            <Button
              variant="ghost"
              type="button"
              onClick={() => setStep(1)}
              disabled={step === 1 || createWorkspace.isPending}
            >
              {t('buttonBack')}
            </Button>
            {step === 1 ? (
              <Button
                type="button"
                onClick={() => setStep(2)}
                disabled={!canAdvance}
              >
                {t('buttonNext')}
              </Button>
            ) : (
              <Button
                type="button"
                onClick={() => void handleCreate()}
                disabled={createWorkspace.isPending}
              >
                {createWorkspace.isPending ? t('buttonCreating') : t('buttonCreate')}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

// ── Step subcomponents ────────────────────────────────────────────────


type Step1Translations = {
  title: string;
  description: string;
  nameLabel: string;
  namePlaceholder: string;
  slugLabel: string;
  slugHint: string;
  languageLabel: string;
};

function Step1({
  name,
  setName,
  slug,
  setSlug,
  language,
  setLanguage,
  inlineError,
  translations,
}: {
  name: string;
  setName: (value: string) => void;
  slug: string;
  setSlug: (value: string) => void;
  language: 'tr' | 'en';
  setLanguage: (value: 'tr' | 'en') => void;
  inlineError: string | null;
  translations: Step1Translations;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-medium">{translations.title}</h3>
        <p className="text-sm text-muted-foreground">{translations.description}</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="workspace-name">{translations.nameLabel}</Label>
        <Input
          id="workspace-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={translations.namePlaceholder}
          autoFocus
          required
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="workspace-slug">{translations.slugLabel}</Label>
        <Input
          id="workspace-slug"
          value={slug}
          onChange={(event) => setSlug(event.target.value)}
          placeholder="acme-labs"
        />
        <p className="text-xs text-muted-foreground">{translations.slugHint}</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="workspace-language">{translations.languageLabel}</Label>
        <Select value={language} onValueChange={(value) => setLanguage(value as 'tr' | 'en')}>
          <SelectTrigger id="workspace-language">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="tr">Türkçe</SelectItem>
            <SelectItem value="en">English</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {inlineError && (
        <p role="alert" className="text-sm text-destructive">
          {inlineError}
        </p>
      )}
    </div>
  );
}

type Step2Translations = {
  title: string;
  description: string;
  starterTitle: string;
  starterPrice: string;
  starterFeatures: string;
  starterRecommended: string;
};

function Step2({ translations }: { translations: Step2Translations }) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-medium">{translations.title}</h3>
        <p className="text-sm text-muted-foreground">{translations.description}</p>
      </div>

      <Card className="border-primary/50">
        <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
          <CardTitle className="text-base">{translations.starterTitle}</CardTitle>
          <span className="text-sm font-semibold">{translations.starterPrice}</span>
        </CardHeader>
        <CardContent className="pt-0">
          <p className="text-sm text-muted-foreground">{translations.starterFeatures}</p>
        </CardContent>
      </Card>

      <p className="text-xs text-muted-foreground">{translations.starterRecommended}</p>
    </div>
  );
}
