import { expect, it } from 'vitest';

it('故意失败以验证 required gate 会拒绝应用 job 失败', () => {
  expect('ui-job-failure').toBe('ui-job-success');
});
