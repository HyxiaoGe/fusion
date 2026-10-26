import { describe, expect, it } from 'vitest';

import { creditsExhaustedMessage } from './credits';

describe('creditsExhaustedMessage', () => {
  it('有重置卡时提示去额度里用卡，否则游客引导登录、登录用户告知恢复时间', () => {
    expect(creditsExhaustedMessage('weekly', false, true)).toBe('本周额度已用完，可在额度里使用重置卡');
    expect(creditsExhaustedMessage('daily', false)).toBe('今日额度已用完，明天 0 点恢复');
    expect(creditsExhaustedMessage('daily', true)).toBe('今天免登录额度已用完，登录后继续');
  });
});
