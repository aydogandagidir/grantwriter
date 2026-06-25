import { setRequestLocale } from 'next-intl/server';

import { OnboardingWizard } from '@/app/[locale]/onboarding/onboarding-wizard';

/**
 * Post-signup workspace bootstrap. The Supabase user exists but has
 * no ``public.users`` row yet — :file:`onboarding-wizard.tsx` collects
 * the workspace name + plan and calls
 * ``POST /api/v1/onboarding/workspace`` to create the tenant + link
 * the caller as its first owner.
 */
export default async function OnboardingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <OnboardingWizard />;
}
