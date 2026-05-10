import { createNavigation } from 'next-intl/navigation';

import { routing } from './routing';

// Locale-aware drop-in replacements for next/link, useRouter, redirect.
// Always import from here in app code so locale-prefix handling stays
// centralised.
export const { Link, redirect, usePathname, useRouter, getPathname } = createNavigation(routing);
