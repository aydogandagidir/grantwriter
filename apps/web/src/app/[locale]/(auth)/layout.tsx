import type { ReactNode } from 'react';

/**
 * Minimal layout for the unauthenticated routes (login, signup,
 * invitation preview). No sidebar — just a centred card.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 px-4 py-12">
      <div className="w-full max-w-md">{children}</div>
    </div>
  );
}
