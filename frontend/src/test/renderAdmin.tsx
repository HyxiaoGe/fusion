import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';
import { AdminAccessProvider } from '@/components/admin/AdminAccessContext';

export function renderAdmin(ui: ReactElement): RenderResult {
  return render(<AdminAccessProvider>{ui}</AdminAccessProvider>);
}
