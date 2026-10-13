'use client';

import ThemeClassSync from '@/components/layouts/ThemeClassSync';
import { Provider } from 'react-redux';
import { store } from './store';

// 主提供器组件
export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <Provider store={store}>
      <ThemeClassSync />
      {children}
    </Provider>
  );
}
