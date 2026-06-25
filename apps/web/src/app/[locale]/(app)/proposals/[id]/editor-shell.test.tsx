/**
 * Smoke tests for the section-tabs wire-up.
 *
 * The actual TipTap editor is heavy to render in jsdom, so we stub
 * ``ProposalEditor`` + ``useProposal`` and assert:
 *  - clicking a section tab updates the editor's ``section`` prop +
 *    its ``initialMarkdown`` (the right per-section markdown reaches
 *    the editor)
 *  - the right rail's tabs (Validation / Provenance / Versions /
 *    Comments) still render alongside the section tabs without
 *    aria-label collisions.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import enMessages from '@/messages/en.json';

import { ProposalEditorShell } from './editor-shell';

// ── Mocks ──────────────────────────────────────────────────────────────

const useProposalMock = vi.fn();
vi.mock('@/lib/api/queries', () => ({
  useProposal: (id: string) => useProposalMock(id),
  // The right rail still consumes other hooks via its own components;
  // we stub them out by mocking the components themselves below.
}));

interface FakeEditorProps {
  section: string;
  initialMarkdown?: string;
}
const proposalEditorCalls: FakeEditorProps[] = [];
vi.mock('@/components/proposal/proposal-editor', () => ({
  ProposalEditor: (props: FakeEditorProps) => {
    proposalEditorCalls.push({
      section: props.section,
      initialMarkdown: props.initialMarkdown,
    });
    return (
      <div data-testid="fake-editor" data-section={props.section}>
        {props.initialMarkdown}
      </div>
    );
  },
}));

// The right-rail panels do their own data fetching — stub them too
// so the test doesn't drag QueryProvider behaviour into scope.
vi.mock('@/components/proposal/validation-panel', () => ({
  ValidationPanel: () => <div data-testid="validation-panel" />,
}));
vi.mock('@/components/proposal/provenance-panel', () => ({
  ProvenancePanel: () => <div data-testid="provenance-panel" />,
}));
vi.mock('@/components/proposal/versions-panel', () => ({
  VersionsPanel: () => <div data-testid="versions-panel" />,
}));
vi.mock('@/components/proposal/comments-panel', () => ({
  CommentsPanel: () => <div data-testid="comments-panel" />,
}));

beforeEach(() => {
  useProposalMock.mockReset();
  proposalEditorCalls.length = 0;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderShell() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <ProposalEditorShell
          proposalId="00000000-0000-0000-0000-000000000001"
          currentUserId="user-1"
        />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

describe('ProposalEditorShell — section tabs', () => {
  it('feeds the excellence markdown to the editor by default', () => {
    useProposalMock.mockReturnValue({
      isPending: false,
      data: {
        id: 'p1',
        title: 'Pilot',
        status: 'draft_complete',
        language: 'en',
        programme_id: 'horizon_eu_ria',
        draft: {
          excellence_md: 'Excellence body sentence.',
          impact_md: 'Impact body sentence.',
          implementation_md: 'Implementation body sentence.',
        },
      },
    });
    renderShell();
    const last = proposalEditorCalls.at(-1);
    expect(last?.section).toBe('excellence');
    expect(last?.initialMarkdown).toBe('Excellence body sentence.');
  });

  it('feeds the impact markdown after the user activates the Impact tab', async () => {
    useProposalMock.mockReturnValue({
      isPending: false,
      data: {
        id: 'p1',
        title: 'Pilot',
        status: 'draft_complete',
        language: 'en',
        programme_id: 'horizon_eu_ria',
        draft: {
          excellence_md: 'EXC',
          impact_md: 'IMP',
          implementation_md: 'IMPL',
        },
      },
    });
    renderShell();
    const user = userEvent.setup();

    // Two ``tablist`` groups render (section tabs on the left,
    // collaboration tabs on the right) — disambiguate by the ARIA
    // label we attach to the section group.
    const sectionTabs = screen.getByRole('tablist', {
      name: /proposal sections/i,
    });
    const impactTab = within(sectionTabs).getByRole('tab', { name: /impact/i });
    await user.click(impactTab);

    const last = proposalEditorCalls.at(-1);
    expect(last?.section).toBe('impact');
    expect(last?.initialMarkdown).toBe('IMP');
  });

  it('falls back to an empty initialMarkdown when the draft is missing', () => {
    useProposalMock.mockReturnValue({
      isPending: true,
      data: undefined,
    });
    renderShell();
    const last = proposalEditorCalls.at(-1);
    expect(last?.initialMarkdown).toBe('');
  });
});
