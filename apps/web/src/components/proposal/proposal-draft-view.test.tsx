import type { ProposalDetail } from '@bluedev/shared-types';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enMessages from '@/messages/en.json';
import { apiClient } from '@/lib/api/client';

import { ProposalDraftView } from './proposal-draft-view';

// Export-download tests drive a mutation + job poll; everything else in
// this file never touches the network, so a file-scoped mock is safe.
vi.mock('@/lib/api/client', () => ({
  apiClient: vi.fn(),
}));

const apiClientMock = vi.mocked(apiClient);

// Use the real English message bundle (not a hand-maintained subset) so the
// provider resolves every key the component tree touches — including the
// `proposalEditor` namespace the mounted TipTap editor + inline AI menu read.
// A partial fixture drifts and floods the test output with next-intl
// MISSING_MESSAGE warnings; the real bundle keeps assertions honest too.
const messages = enMessages;

const baseProposal: ProposalDetail = {
  id: '00000000-0000-0000-0000-000000000001',
  tenant_id: '00000000-0000-0000-0000-000000000002',
  programme_id: 'horizon_eu_ria',
  language: 'en',
  title: 'Test',
  acronym: null,
  status: 'draft_complete',
  call_id: null,
  created_by: '00000000-0000-0000-0000-000000000003',
  created_at: '2026-05-12T00:00:00Z',
  updated_at: null,
  brief: {},
  draft: {},
  compliance_report: {},
  distinctiveness_score: null,
  ai_disclosure_text: null,
  word_count: 0,
  page_count: 0,
  llm_cost_usd: 0,
};

function renderView(proposal: ProposalDetail) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="en" messages={messages}>
        <ProposalDraftView proposal={proposal} />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiClientMock.mockReset();
});

describe('<ProposalDraftView>', () => {
  it('shows the empty-state hint when no draft sections exist', () => {
    renderView(baseProposal);
    expect(screen.getByText(/No draft yet/i)).toBeInTheDocument();
  });

  it('renders the section markdown when the draft has content', () => {
    renderView({
      ...baseProposal,
      draft: {
        excellence_md: '## 1.1 Objectives\nfoo',
        impact_md: '## 2.1 Pathways\nbar',
        implementation_md: '## 3.1 WPs\nbaz',
      },
    });
    expect(screen.getByTestId('section-excellence')).toBeInTheDocument();
    expect(screen.getByText(/1.1 Objectives/)).toBeInTheDocument();
    expect(screen.getByText(/2.1 Pathways/)).toBeInTheDocument();
    expect(screen.getByText(/3.1 WPs/)).toBeInTheDocument();
  });

  it('exposes both DOCX + Lump Sum XLSX export buttons for Horizon Europe RIA', () => {
    renderView({
      ...baseProposal,
      programme_id: 'horizon_eu_ria',
      draft: { excellence_md: 'x' },
    });
    expect(screen.getByText('Export DOCX')).toBeInTheDocument();
    expect(screen.getByText('Export Lump Sum XLSX')).toBeInTheDocument();
  });

  it('hides the XLSX button for non-Horizon programmes', () => {
    renderView({
      ...baseProposal,
      programme_id: 'tubitak_1501',
      draft: { excellence_md: 'x' },
    });
    expect(screen.getByText('Export DOCX')).toBeInTheDocument();
    expect(screen.queryByText('Export Lump Sum XLSX')).not.toBeInTheDocument();
  });

  it('polls the export job and renders the signed-URL download link', async () => {
    apiClientMock.mockImplementation((path: string) => {
      if (path.includes('/export')) {
        return Promise.resolve({
          job_id: 'job-42',
          status: 'queued',
          proposal_id: baseProposal.id,
          format: 'docx',
        });
      }
      if (path.includes('/jobs/job-42')) {
        return Promise.resolve({
          id: 'job-42',
          status: 'completed',
          result: { signed_url: 'https://files.example/agy100.docx' },
          error: null,
        });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    renderView({ ...baseProposal, draft: { excellence_md: 'x' } });

    fireEvent.click(screen.getByTestId('export-docx'));

    const link = await waitFor(() => screen.getByTestId('export-download'));
    expect(link).toHaveAttribute('href', 'https://files.example/agy100.docx');
    expect(link).toHaveTextContent('Download file');
  });

  it('surfaces a failed export with its error message', async () => {
    apiClientMock.mockImplementation((path: string) => {
      if (path.includes('/export')) {
        return Promise.resolve({
          job_id: 'job-43',
          status: 'queued',
          proposal_id: baseProposal.id,
          format: 'docx',
        });
      }
      if (path.includes('/jobs/job-43')) {
        return Promise.resolve({
          id: 'job-43',
          status: 'failed',
          result: null,
          error: 'Bucket not found',
        });
      }
      return Promise.reject(new Error(`unexpected path: ${path}`));
    });

    renderView({ ...baseProposal, draft: { excellence_md: 'x' } });

    fireEvent.click(screen.getByTestId('export-docx'));

    await waitFor(() =>
      expect(screen.getByTestId('export-status')).toHaveTextContent(
        'Export failed: Bucket not found',
      ),
    );
  });
});
