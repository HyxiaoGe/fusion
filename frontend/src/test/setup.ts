import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { resetBackgroundSchedulerForTests } from '@/lib/scheduler/backgroundScheduler';

// 调度器是模块级单例：每个用例结束后清空任务、卸下监听，避免跨用例串扰。
afterEach(() => {
  resetBackgroundSchedulerForTests();
});
