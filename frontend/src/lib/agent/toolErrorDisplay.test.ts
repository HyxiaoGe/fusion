import { describe, expect, it } from 'vitest';

import { getToolErrorDisplay, getUrlReadFailureReason } from './toolErrorDisplay';

describe('getUrlReadFailureReason', () => {
  it('展示后端固定的读取原因，去掉「已跳过该来源」', () => {
    expect(getUrlReadFailureReason('网页不存在（链接已失效或地址有误），已跳过该来源')).toBe(
      '网页不存在（链接已失效或地址有误）',
    );
    expect(getUrlReadFailureReason('网页读取超时，已跳过该来源')).toBe('网页读取超时');
  });

  it('其他文本可能含底层错误，统一显示未能读取', () => {
    expect(getUrlReadFailureReason('reader-service 读取超时，已降级跳过')).toBe('未能读取');
    expect(getUrlReadFailureReason('HTTP 404')).toBe('未能读取');
    expect(getUrlReadFailureReason(undefined)).toBe('未能读取');
  });
});

describe('getToolErrorDisplay', () => {
  it('url_read 失败用中性原因，中断单独说明', () => {
    expect(getToolErrorDisplay('url_read', 'degraded', '网页不存在（链接已失效或地址有误），已跳过该来源')).toBe(
      '网页不存在（链接已失效或地址有误）',
    );
    expect(getToolErrorDisplay('url_read', 'failed', 'HTTP 404')).toBe('未能读取');
    expect(getToolErrorDisplay('url_read', 'interrupted')).toBe('网页读取已中断');
  });
});
