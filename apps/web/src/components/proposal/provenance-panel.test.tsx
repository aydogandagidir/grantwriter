import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import enMessages from '@/messages/en.json';

import { ProvenancePanel } from './provenance-panel';

// Hoisted mock so the `useProvenanceStats` import in the component
// resolves to a configurable AsyncMock without needing real fetch.
const useProvenanceStatsMock = vi.fn();
vi.mock('@/lib/api/queries', () => ({
  useProvenanceStats: (id: string) => useProvenanceStatsMock(id),
}));

function renderPanel(proposalId = '00000000-0000-0000-0000-000000000001') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <ProvenancePanel proposalId={proposalId} />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useProvenanceStatsMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProvenancePanel', () => {
  it('shows a loading state while the query is pending', () => {
    useProvenanceStatsMock.mockReturnValue({
      isPending: true,
      isError: false,
      data: undefined,
    });
    renderPanel();
    expect(screen.getByText(/loading provenance/i)).toBeInTheDocument();
  });

  it('renders the empty description when there are no sentences', () => {
    useProvenanceStatsMock.mockReturnValue({
      isPending: false,
      isError: false,
      data: { total: 0, per_source: [], per_agent: [], per_model: [] },
    });
    renderPanel();
    expect(
      screen.getByText(/provenance starts populating/i),
    ).toBeInTheDocument();
  });

  it('renders per-source bars + disclosure block on success', () => {
    useProvenanceStatsMock.mockReturnValue({
      isPending: false,
      isError: false,
      data: {
        total: 100,
        per_source: [
          { source: 'human', count: 60 },
          { source: 'ai-generated', count: 40 },
        ],
        per_agent: [{ source: 'excellence_writer', count: 40 }],
        per_model: [{ source: 'anthropic/claude-opus-4-7', count: 40 }],
      },
    });
    renderPanel();
    expect(screen.getByText(/100 sentences tracked/i)).toBeInTheDocument();
    // Both source rows render with their counts visible.
    expect(screen.getByText('Human-written')).toBeInTheDocument();
    expect(screen.getByText('AI-generated')).toBeInTheDocument();
    // Bars publish their value via role="progressbar" + aria-valuenow.
    const bars = screen.getAllByRole('progressbar');
    expect(bars).toHaveLength(2);
    expect(bars[0]).toHaveAttribute('aria-valuenow', '60');
    expect(bars[1]).toHaveAttribute('aria-valuenow', '40');

    // Disclosure text is present (inside <details>).
    const disclosure = screen.getByTestId('provenance-disclosure-text');
    expect(disclosure.textContent).toContain('Total sentences: 100');
    expect(disclosure.textContent).toContain('Human-written: 60 sentences');
  });

  it('copies the disclosure to the clipboard when the button is pressed', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    // jsdom's navigator.clipboard is undefined; install our stub so the
    // component's handler doesn't trip on the missing API.
    Object.defineProperty(globalThis.navigator, 'clipboard', {
      configurable: true,
      writable: true,
      value: { writeText },
    });

    useProvenanceStatsMock.mockReturnValue({
      isPending: false,
      isError: false,
      data: {
        total: 10,
        per_source: [{ source: 'human', count: 10 }],
        per_agent: [],
        per_model: [],
      },
    });
    renderPanel();

    // jsdom doesn't toggle <details> on summary click, so open it
    // programmatically — the button is in the DOM either way thanks to
    // React, but assertion ergonomics improve with the panel expanded.
    const details = document.querySelector('details');
    if (details) details.setAttribute('open', 'true');

    const button = screen.getByRole('button', { name: /copy disclosure/i });
    fireEvent.click(button);

    // handleCopy is async — await the spy + the button text flip.
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const firstCall = writeText.mock.calls[0];
    const text = (firstCall?.[0] ?? '') as string;
    expect(text).toContain('Total sentences: 10');
    expect(
      await screen.findByRole('button', { name: /copied/i }),
    ).toBeInTheDocument();
  });
});
