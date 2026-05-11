import type { Metadata } from 'next';
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, setRequestLocale } from 'next-intl/server';
import { notFound } from 'next/navigation';
import type { ReactNode } from 'react';

import { Providers } from '@/components/providers';
import { Toaster } from '@/components/ui/toaster';
import { routing, type Locale } from '@/i18n/routing';

import '@/app/globals.css';

export const metadata: Metadata = {
  title: 'Bluedev GrantWriter',
  description: 'AI-destekli, compliance-onaylı, iki dilli (TR/EN) hibe yazımı SaaS',
};

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!(routing.locales as readonly string[]).includes(locale)) {
    notFound();
  }
  const typedLocale = locale as Locale;
  setRequestLocale(typedLocale);

  const messages = await getMessages();

  return (
    <html lang={locale} suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans antialiased">
        <NextIntlClientProvider messages={messages} locale={typedLocale}>
          <Providers>{children}</Providers>
          <Toaster />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
