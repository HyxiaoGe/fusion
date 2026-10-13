'use client';

import ThemeClassSync from '@/components/layouts/ThemeClassSync';
import { dropLegacyLocalDatabase } from '@/lib/legacyLocalDb';
import { useEffect } from 'react';
import { Provider } from 'react-redux';
import { store } from './store';

// 主提供器组件
export function Providers({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    dropLegacyLocalDatabase();
  }, []);

  return (
    <Provider store={store}>
      <ThemeClassSync />
      {children}
    </Provider>
  );
}
