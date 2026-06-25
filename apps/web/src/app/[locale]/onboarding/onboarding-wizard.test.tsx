import '@testing-library/jest-dom/vitest';

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderWithProviders } from '@/test/render-with-providers';

// Mock next/navigation router — vitest's jsdom doesn't ship the App
// Router. We only need to assert the success path calls `replace`.
const replaceSpy = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: replaceSpy, push: vi.fn() }),
  usePathname: () => '/tr/onboarding',
}));

// Mock the workspace-creation hook so the wizard doesn't actually hit
// the network. The default returns a resolved Promise; specific tests
// can override `mutateAsync` to assert error mapping.
const mutateAsyncMock = vi.fn().mockResolvedValue({
  tenant_id: 'tenant-uuid',
  slug: 'acme-labs',
  role: 'owner',
  plan: 'starter',
});

vi.mock('@/lib/api/queries', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/queries')>(
    '@/lib/api/queries',
  );
  return {
    ...actual,
    useCreateWorkspace: () => ({
      mutateAsync: mutateAsyncMock,
      isPending: false,
    }),
  };
});

import { OnboardingWizard } from './onboarding-wizard';

afterEach(() => {
  replaceSpy.mockClear();
  mutateAsyncMock.mockClear();
  mutateAsyncMock.mockResolvedValue({
    tenant_id: 'tenant-uuid',
    slug: 'acme-labs',
    role: 'owner',
    plan: 'starter',
  });
});

describe('OnboardingWizard', () => {
  it('keeps Continue disabled until the name field has >= 2 chars', () => {
    renderWithProviders(<OnboardingWizard />);

    const next = screen.getByRole('button', { name: /devam/i });
    expect(next).toBeDisabled();

    const nameInput = screen.getByLabelText(/workspace adı/i);
    fireEvent.change(nameInput, { target: { value: 'A' } });
    expect(next).toBeDisabled();

    fireEvent.change(nameInput, { target: { value: 'Acme Labs' } });
    expect(next).toBeEnabled();
  });

  it('walks to step 2 and Back returns to step 1', () => {
    renderWithProviders(<OnboardingWizard />);
    fireEvent.change(screen.getByLabelText(/workspace adı/i), {
      target: { value: 'Acme Labs' },
    });
    fireEvent.click(screen.getByRole('button', { name: /devam/i }));

    expect(screen.getByText(/başlangıç planını/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /geri/i }));
    expect(screen.getByLabelText(/workspace adı/i)).toBeInTheDocument();
  });

  it('submits the workspace + redirects to the localised dashboard', async () => {
    renderWithProviders(<OnboardingWizard />);
    fireEvent.change(screen.getByLabelText(/workspace adı/i), {
      target: { value: 'Acme Labs' },
    });
    fireEvent.change(screen.getByLabelText(/url slug/i), {
      target: { value: 'acme-labs' },
    });
    fireEvent.click(screen.getByRole('button', { name: /devam/i }));
    fireEvent.click(screen.getByRole('button', { name: /workspace oluştur/i }));

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledWith({
        name: 'Acme Labs',
        slug: 'acme-labs',
        preferred_language: 'tr',
      });
    });
    expect(replaceSpy).toHaveBeenCalledWith('/tr/dashboard');
  });
});
