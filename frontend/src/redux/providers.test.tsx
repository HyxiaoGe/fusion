import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/components/layouts/ThemeClassSync', () => ({
  default: () => <div data-testid="theme-class-sync" />,
}));

import { Providers } from './providers';

describe('Providers', () => {
  it('renders children immediately with theme sync', () => {
    render(
      <Providers>
        <div>应用内容</div>
      </Providers>
    );

    expect(screen.getByText('应用内容')).toBeTruthy();
    expect(screen.getByTestId('theme-class-sync')).toBeTruthy();
    expect(screen.queryByTestId('chat-loading-app-shell')).toBeNull();
  });
});
