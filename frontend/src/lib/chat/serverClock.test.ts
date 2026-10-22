import { afterEach, describe, expect, it } from 'vitest';
import { localTimeOfEvent, noteServerTime, resetServerClockForTest, toLocalTime } from './serverClock';

describe('serverClock', () => {
  afterEach(() => resetServerClockForTest());

  it('还没有样本时原样返回服务端时间', () => {
    expect(toLocalTime(1_000_000)).toBe(1_000_000);
    expect(localTimeOfEvent(undefined)).toBeUndefined();
    expect(localTimeOfEvent(0)).toBeUndefined();
  });

  it('取延迟最小的样本作为时钟差，回放的旧事件不会拉偏', () => {
    const local = 1_761_100_000_000;
    // 服务端比本机快 5 秒；server_clock 帧几乎没有延迟。
    noteServerTime(local + 5_000, local + 10);
    // 回放 190 秒前的事件：样本里带着事件年龄，偏大，不采用。
    noteServerTime(local + 5_000 - 190_000, local + 20);
    expect(toLocalTime(local + 5_000)).toBe(local + 10);
    expect(localTimeOfEvent((local + 5_000 - 190_000) / 1000)).toBe(local + 10 - 190_000);
  });

  it('样本过期后接受新的时钟差（本机休眠或校时）', () => {
    noteServerTime(105_000, 100_000);
    noteServerTime(2_000_000, 2_000_000 + 60_000 * 11);
    expect(toLocalTime(2_000_000)).toBe(2_000_000 + 60_000 * 11);
  });
});
