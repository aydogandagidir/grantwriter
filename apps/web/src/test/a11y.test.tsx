/**
 * Accessibility smoke tests — axe-core via jest-axe against the page
 * surfaces we ship in Sprint 3. The point isn't full keyboard-flow
 * coverage (that lands in Sprint 4 with Playwright); it's "no
 * regression on the obvious WCAG 2.1 AA rules": missing label, low
 * contrast, missing alt, role/aria mismatches.
 *
 * Each component renders inside the shared TanStack Query +
 * next-intl provider wrapper (see `render-with-providers`). Network-
 * dependent components show their loading skeleton — axe checks that
 * the skeleton state is also accessible, which matters because
 * skeletons are the first paint a user sees.
 */

import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';

import { LoginForm } from '@/app/[locale]/(auth)/login/login-form';
import { SignupForm } from '@/app/[locale]/(auth)/signup/signup-form';
import { AuditTable } from '@/app/[locale]/(app)/settings/audit/audit-table';
import { BillingPanel } from '@/app/[locale]/(app)/settings/billing/billing-panel';
import { InvitationsPanel } from '@/app/[locale]/(app)/settings/invitations/invitations-panel';
import { LlmConfigCard } from '@/app/[locale]/(app)/settings/llm-config/llm-config-card';
import { MembersTable } from '@/app/[locale]/(app)/settings/members/members-table';
import { UsageReport } from '@/app/[locale]/(app)/settings/usage/usage-report';
import { CommentsPanel } from '@/components/proposal/comments-panel';
import { VersionsPanel } from '@/components/proposal/versions-panel';
import { renderWithProviders } from '@/test/render-with-providers';

describe('a11y smoke', () => {
  it('LoginForm', async () => {
    const { container } = renderWithProviders(<LoginForm nextPath="/dashboard" />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('SignupForm', async () => {
    const { container } = renderWithProviders(<SignupForm />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('LlmConfigCard (loading state)', async () => {
    const { container } = renderWithProviders(<LlmConfigCard />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('MembersTable (loading state)', async () => {
    const { container } = renderWithProviders(<MembersTable />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('InvitationsPanel (loading state)', async () => {
    const { container } = renderWithProviders(<InvitationsPanel />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('AuditTable (loading state)', async () => {
    const { container } = renderWithProviders(<AuditTable />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('UsageReport (loading state)', async () => {
    const { container } = renderWithProviders(<UsageReport />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('BillingPanel (loading state)', async () => {
    const { container } = renderWithProviders(<BillingPanel />);
    expect(await axe(container)).toHaveNoViolations();
  });

  it('VersionsPanel (loading state)', async () => {
    const { container } = renderWithProviders(
      <VersionsPanel proposalId="00000000-0000-0000-0000-000000000001" />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });

  it('CommentsPanel (loading state)', async () => {
    const { container } = renderWithProviders(
      <CommentsPanel
        proposalId="00000000-0000-0000-0000-000000000001"
        currentUserId="00000000-0000-0000-0000-000000000002"
      />,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});
