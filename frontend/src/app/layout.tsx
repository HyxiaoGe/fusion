import './globals.css';
import { Providers } from '@/redux/providers';
import ClientLayout from './ClientLayout';
import { cn } from '@/lib/utils';
import { Metadata, Viewport } from 'next';

export const metadata: Metadata = {
  title: 'Fusion',
  description: 'Fusion 智能助手：对话、联网搜索、出行与天气查询、文档创作。',
  // 性能优化的meta标签
  robots: 'index,follow',
  // favicon.ico 由 src/app 文件约定输出；SVG 内部按系统明暗切换下半部颜色。
  icons: {
    icon: [{ url: '/favicon.svg', type: 'image/svg+xml' }],
    apple: '/apple-touch-icon.png',
  },
  manifest: '/site.webmanifest',
  // 预连接到可能的外部资源
  other: {
    'dns-prefetch': '//fonts.googleapis.com',
    'preconnect': 'https://fonts.gstatic.com',
  }
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // 安卓 Chrome 弹出键盘时缩小布局视口，底部输入框留在键盘上方（iOS 忽略该项）。
  interactiveWidget: 'resizes-content',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: 'white' },
    { media: '(prefers-color-scheme: dark)', color: 'black' },
  ],
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        {/* DNS预解析和预连接 */}
        <link rel="dns-prefetch" href="//fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body className={cn(
        "min-h-screen bg-background font-sans antialiased",
        "transition-colors duration-200"
      )}>
        <Providers>
          <ClientLayout>
            {children}
          </ClientLayout>
        </Providers>
      </body>
    </html>
  );
}
