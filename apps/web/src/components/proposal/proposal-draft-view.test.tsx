import type { ProposalDetail } from '@bluedev/shared-types';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { describe, expect, it } from 'vitest';

import { ProposalDraftView } from './proposal-draft-view';

const messages = {
  proposalDetail: {
    'tabs.draft': 'Draft',
    draftEmpty: 'No draft yet. Click "Generate" to run the saga.',
    exportDocx: 'Export DOCX',
    exportXlsx: 'Export Lump Sum XLSX',
    exporting: 'Sending to export…',
    exportQueued: 'Export queued (job {jobId}). Check the jobs API for the download URL.',
  },
};

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
});
