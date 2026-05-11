import { getTranslations, setRequestLocale } from 'next-intl/server';

import { LoginForm } from '@/app/[locale]/(auth)/login/login-form';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';

export default async function LoginPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ next?: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations('auth.login');
  const { next } = await searchParams;

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('subtitle')}</CardDescription>
      </CardHeader>
      <CardContent>
        <LoginForm nextPath={next ?? '/dashboard'} />
        <p className="mt-4 text-center text-sm text-muted-foreground">
          {t('noAccount')}{' '}
          <Link href="/signup" className="font-medium text-primary hover:underline">
            {t('signUp')}
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
