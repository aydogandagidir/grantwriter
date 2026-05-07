import type { Metadata } from 'next';
import type { ReactNode } from 'react';

export const metadata: Metadata = {
  title: 'Bluedev GrantWriter',
  description: 'AI-destekli, compliance-onaylı, iki dilli (TR/EN) hibe yazımı SaaS',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="tr">
      <body>{children}</body>
    </html>
  );
}
