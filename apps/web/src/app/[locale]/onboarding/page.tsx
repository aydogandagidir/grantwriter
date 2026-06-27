import { setRequestLocale } from 'next-intl/server';

import { OnboardingWizard } from '@/components/onboarding/onboarding-wizard';

/**
 * Onboarding page — reached when a Supabase user exists but has no
 * public.users row yet (post-signup). Presents a 2-step wizard:
 *   Step 1: Create a workspace (name + slug)
 *   Step 2: Choose preferred language
 *
 * On success the backend creates the tenant + user row atomically
 * and the wizard redirects to /dashboard.
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
