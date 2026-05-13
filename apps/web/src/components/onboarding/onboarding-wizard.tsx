'use client';

import { zodResolver } from '@hookform/resolvers/zod';
import { Building2, ChevronRight, Globe, Loader2, Rocket } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

import { capture, identify } from '@/lib/analytics';
import { captureException } from '@/lib/sentry';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { apiClient } from '@/lib/api/client';
import { cn } from '@/lib/utils';

// ── Schema ────────────────────────────────────────────────────────────

const slugRegex = /^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$/;

const onboardingSchema = z.object({
  name: z
    .string()
    .min(2, 'Workspace name must be at least 2 characters')
    .max(100, 'Workspace name must be at most 100 characters'),
  slug: z
    .string()
    .min(3, 'Slug must be at least 3 characters')
    .max(50, 'Slug must be at most 50 characters')
    .regex(slugRegex, 'Only lowercase letters, numbers, and hyphens. Must start/end with letter or number.'),
  preferred_language: z.enum(['tr', 'en']),
});

type OnboardingFormValues = z.infer<typeof onboardingSchema>;

interface OnboardingResponse {
  tenant_id: string;
  tenant_name: string;
  tenant_slug: string;
  user_id: string;
  role: string;
}

// ── Component ─────────────────────────────────────────────────────────

export function OnboardingWizard() {
  const t = useTranslations('onboarding');
  const router = useRouter();
  const [step, setStep] = useState<1 | 2>(1);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const form = useForm<OnboardingFormValues>({
    resolver: zodResolver(onboardingSchema),
    defaultValues: {
      name: '',
      slug: '',
      preferred_language: 'tr',
    },
    mode: 'onChange',
  });

  const watchName = form.watch('name');

  // Auto-generate slug from name
  const handleNameChange = (value: string) => {
    form.setValue('name', value);
    const autoSlug = value
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, '')
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 50);
    if (autoSlug.length >= 3) {
      form.setValue('slug', autoSlug, { shouldValidate: true });
    }
  };

  const handleNextStep = async () => {
    const valid = await form.trigger(['name', 'slug']);
    if (valid) setStep(2);
  };

  const handleSubmit = async (values: OnboardingFormValues) => {
    setIsSubmitting(true);
    setError(null);

    try {
      const result = await apiClient<OnboardingResponse>('/api/v1/onboarding', {
        method: 'POST',
        body: values,
      });

      // Analytics: identify user + track onboarding completion. Both
      // calls are no-ops if PostHog isn't configured (initAnalytics()
      // returns early when NEXT_PUBLIC_POSTHOG_KEY is unset).
      identify(result.user_id, {
        role: result.role,
        tenant_id: result.tenant_id,
        preferred_language: values.preferred_language,
      });
      capture('onboarding_completed', {
        tenant_slug: result.tenant_slug,
        preferred_language: values.preferred_language,
      });

      router.push('/dashboard');
    } catch (err: unknown) {
      // Surface a user-readable message + report to Sentry. The
      // ``detail`` extraction handles two shapes that the backend may
      // return: a plain string (most 4xx/5xx) or a Pydantic validation
      // array (422 — keys vary, so we fall back to a generic message
      // rather than dump structured noise in the UI).
      captureException(err, { tags: { flow: 'onboarding' } });
      const detail =
        err && typeof err === 'object' && 'detail' in err
          ? (err as { detail: unknown }).detail
          : undefined;
      setError(typeof detail === 'string' ? detail : t('errorGeneric'));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-background via-muted/30 to-primary/5 px-4 py-12">
      <Card className="w-full max-w-lg border-0 shadow-xl shadow-primary/5">
        {/* Progress indicator */}
        <div className="flex items-center justify-center gap-3 pt-6">
          <StepDot active={step >= 1} label="1" />
          <div className={cn('h-0.5 w-8 rounded-full transition-colors', step >= 2 ? 'bg-primary' : 'bg-muted')} />
          <StepDot active={step >= 2} label="2" />
        </div>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)}>
            {step === 1 && (
              <>
                <CardHeader className="text-center">
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Building2 className="h-6 w-6" />
                  </div>
                  <CardTitle className="text-2xl">{t('step1Title')}</CardTitle>
                  <CardDescription className="text-base">{t('step1Subtitle')}</CardDescription>
                </CardHeader>

                <CardContent className="space-y-5 pb-8">
                  <FormField
                    control={form.control}
                    name="name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>{t('workspaceName')}</FormLabel>
                        <FormControl>
                          <Input
                            id="onboarding-workspace-name"
                            placeholder={t('workspaceNamePlaceholder')}
                            {...field}
                            onChange={(e) => handleNameChange(e.target.value)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="slug"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>{t('workspaceSlug')}</FormLabel>
                        <FormControl>
                          <Input
                            id="onboarding-workspace-slug"
                            placeholder="my-company"
                            {...field}
                          />
                        </FormControl>
                        <FormDescription>
                          {t('slugDescription', { domain: `app.bluedev.dev/${form.getValues('slug') || '...'}` })}
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <Button
                    id="onboarding-next"
                    type="button"
                    className="w-full"
                    onClick={handleNextStep}
                    disabled={!watchName || watchName.length < 2}
                  >
                    {t('next')}
                    <ChevronRight className="ml-2 h-4 w-4" />
                  </Button>
                </CardContent>
              </>
            )}

            {step === 2 && (
              <>
                <CardHeader className="text-center">
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
                    <Globe className="h-6 w-6" />
                  </div>
                  <CardTitle className="text-2xl">{t('step2Title')}</CardTitle>
                  <CardDescription className="text-base">{t('step2Subtitle')}</CardDescription>
                </CardHeader>

                <CardContent className="space-y-5 pb-8">
                  <FormField
                    control={form.control}
                    name="preferred_language"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>{t('preferredLanguage')}</FormLabel>
                        <FormControl>
                          <div className="grid grid-cols-2 gap-3">
                            <LanguageOption
                              value="tr"
                              label="Türkçe"
                              description={t('languageTrDesc')}
                              selected={field.value === 'tr'}
                              onClick={() => field.onChange('tr')}
                            />
                            <LanguageOption
                              value="en"
                              label="English"
                              description={t('languageEnDesc')}
                              selected={field.value === 'en'}
                              onClick={() => field.onChange('en')}
                            />
                          </div>
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {error && (
                    <div className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
                      {error}
                    </div>
                  )}

                  <div className="flex gap-3">
                    <Button
                      id="onboarding-back"
                      type="button"
                      variant="outline"
                      className="flex-1"
                      onClick={() => setStep(1)}
                      disabled={isSubmitting}
                    >
                      {t('back')}
                    </Button>
                    <Button
                      id="onboarding-submit"
                      type="submit"
                      className="flex-1"
                      disabled={isSubmitting}
                    >
                      {isSubmitting ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          {t('creating')}
                        </>
                      ) : (
                        <>
                          <Rocket className="mr-2 h-4 w-4" />
                          {t('createWorkspace')}
                        </>
                      )}
                    </Button>
                  </div>
                </CardContent>
              </>
            )}
          </form>
        </Form>
      </Card>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────

function StepDot({ active, label }: { active: boolean; label: string }) {
  return (
    <div
      className={cn(
        'flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold transition-all',
        active
          ? 'bg-primary text-primary-foreground shadow-md shadow-primary/30'
          : 'bg-muted text-muted-foreground',
      )}
    >
      {label}
    </div>
  );
}

function LanguageOption({
  value,
  label,
  description,
  selected,
  onClick,
}: {
  value: string;
  label: string;
  description: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      id={`onboarding-lang-${value}`}
      type="button"
      onClick={onClick}
      className={cn(
        'flex flex-col items-center gap-1 rounded-lg border-2 p-4 text-center transition-all',
        selected
          ? 'border-primary bg-primary/5 text-foreground shadow-sm'
          : 'border-muted bg-card text-muted-foreground hover:border-border hover:bg-muted/50',
      )}
    >
      <span className="text-sm font-semibold">{label}</span>
      <span className="text-xs">{description}</span>
    </button>
  );
}
