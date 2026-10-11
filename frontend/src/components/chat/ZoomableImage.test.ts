import { describe, expect, it } from 'vitest';

import { IDENTITY_TRANSFORM, MAX_ZOOM, zoomAround } from './ZoomableImage';

describe('zoomAround', () => {
  const center = { x: 100, y: 100 };

  it('keeps the content under the anchor fixed while zooming', () => {
    const anchor = { x: 150, y: 80 };
    const zoomed = zoomAround(IDENTITY_TRANSFORM, 2, anchor, center);
    // 锚点下的图片局部坐标在缩放前后应映射到同一屏幕位置。
    const local = { x: anchor.x - center.x, y: anchor.y - center.y };
    expect(center.x + zoomed.x + zoomed.scale * local.x).toBeCloseTo(anchor.x);
    expect(center.y + zoomed.y + zoomed.scale * local.y).toBeCloseTo(anchor.y);

    const further = zoomAround(zoomed, 4, anchor, center);
    expect(center.x + further.x + further.scale * local.x).toBeCloseTo(anchor.x);
  });

  it('clamps the scale and resets the offset at 1', () => {
    expect(zoomAround(IDENTITY_TRANSFORM, 100, center, center).scale).toBe(MAX_ZOOM);
    expect(zoomAround({ scale: 3, x: 40, y: -20 }, 0.5, { x: 0, y: 0 }, center)).toEqual(IDENTITY_TRANSFORM);
  });
});
