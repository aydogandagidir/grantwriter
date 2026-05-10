import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { Button } from './button';

describe('<Button>', () => {
  it('renders a real button by default', () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole('button', { name: 'Save' })).toBeInTheDocument();
  });

  it('applies variant + size classes via cn() merge', () => {
    render(
      <Button variant="destructive" size="sm">
        Delete
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Delete' });
    // bg-destructive comes from variant, h-9 from size; we don't pin the
    // full class string (twMerge order varies) — just spot-check both.
    expect(button.className).toContain('bg-destructive');
    expect(button.className).toContain('h-9');
  });

  it('forwards onClick', async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Click me</Button>);
    await userEvent.click(screen.getByRole('button', { name: 'Click me' }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('does not fire onClick when disabled', async () => {
    const onClick = vi.fn();
    render(
      <Button onClick={onClick} disabled>
        Disabled
      </Button>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Disabled' }));
    expect(onClick).not.toHaveBeenCalled();
  });

  it('renders as an anchor when asChild is set', () => {
    render(
      <Button asChild>
        <a href="/dashboard">Go</a>
      </Button>,
    );
    const link = screen.getByRole('link', { name: 'Go' });
    expect(link).toHaveAttribute('href', '/dashboard');
    // Picks up the variant classes via Slot composition.
    expect(link.className).toContain('inline-flex');
  });
});
