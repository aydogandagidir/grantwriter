import { getTranslations, setRequestLocale } from 'next-intl/server';

import { SignupForm } from '@/app/[locale]/(auth)/signup/signup-form';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';

export default async function SignupPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations('auth.signup');

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('subtitle')}</CardDescription>
      </CardHeader>
      <CardContent>
        <SignupForm />
        <p className="mt-4 text-center text-sm text-muted-foreground">
          {t('haveAccount')}{' '}
          <Link href="/login" className="font-medium text-primary hover:underline">
            {t('logIn')}
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
