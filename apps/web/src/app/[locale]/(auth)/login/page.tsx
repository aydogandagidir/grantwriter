import { getTranslations, setRequestLocale } from 'next-intl/server';

import { LoginForm } from '@/app/[locale]/(auth)/login/login-form';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';

export default async function LoginPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string }>;
  searchParams: Promise<{ next?: string; auth_error?: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations('auth.login');
  const { next, auth_error: authError } = await searchParams;

  // The /auth/callback handler bounces here with ?auth_error=<code> when
  // an email confirmation / magic link is expired, already used, or the
  // code exchange fails. Surface a readable message instead of a silent
  // redirect so the user knows to request a fresh link.
  const authErrorMessage = authError
    ? t.has(`authErrors.${authError}`)
      ? t(`authErrors.${authError}`)
      : t('authErrors.generic')
    : null;

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('subtitle')}</CardDescription>
      </CardHeader>
      <CardContent>
        {authErrorMessage ? (
          <div
            role="alert"
            className="mb-4 rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {authErrorMessage}
          </div>
        ) : null}
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
