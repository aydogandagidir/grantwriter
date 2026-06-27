import { setRequestLocale } from 'next-intl/server';

import { OrganizationProfileForm } from '@/app/[locale]/(app)/organization/organization-profile-form';

export default async function OrganizationPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <OrganizationProfileForm />;
}
