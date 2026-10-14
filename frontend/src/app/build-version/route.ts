// 线上当前前端版本；已打开的页面拿它和自己构建时的版本比对，判断是否有新版本。
export const dynamic = 'force-dynamic';

export function GET() {
  return Response.json(
    { version: process.env.NEXT_PUBLIC_BUILD_VERSION ?? '' },
    { headers: { 'Cache-Control': 'no-store' } },
  );
}
