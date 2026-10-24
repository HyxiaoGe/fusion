import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Image from 'next/image';
import { Sparkles, Wand2 } from 'lucide-react';
import {
  fetchPromptExamples,
  type PromptExample,
} from '@/lib/api/prompts';
import type { Entitlements } from '@/lib/api/user';
import { preloadChatMessageList } from '@/components/lazy/preloaders';
import { useRenderProbe } from '@/lib/debug/perfProbe';
import { cn } from '@/lib/utils';
import glassSurface from '@/components/ui/GlassSurface.module.css';
import GlassHoverLens, { pointGlassLight, resetGlassLight } from '@/components/ui/GlassHoverLens';

interface ShowcaseImage {
  id: string;
  src: string;
  /** 卡片角标：这张图代表的风格或能力 */
  label: string;
  /** 点击后填入输入框的提示词，用户可改了再发 */
  prompt: string;
}

// 从过往生成的图里挑的代表作，覆盖插画、海报、带中文字、头像、写实摄影几类；不放真实品牌和 IP。
const SHOWCASE_IMAGES: ShowcaseImage[] = [
  {
    id: 'watercolor-cat',
    src: '/assets/home-showcase/watercolor-cat.webp',
    label: '水彩插画',
    prompt: '画一只戴红白格纹围巾的橘猫坐在雪地里，水彩风格，温馨治愈',
  },
  {
    id: 'cafe-opening-poster',
    src: '/assets/home-showcase/cafe-opening-poster.webp',
    label: '中文海报',
    prompt: '帮我做一张竖版咖啡馆开业海报，暖色调，主标题「新店开业」，副标题「香气与暖意，正式营业」',
  },
  {
    id: 'cyberpunk-rain',
    src: '/assets/home-showcase/cyberpunk-rain.webp',
    label: '电影感海报',
    prompt: '画一张竖版赛博朋克雨夜街景海报：霓虹招牌倒映在湿漉漉的街面上，一个穿风衣的人背对镜头走在雨中',
  },
  {
    id: 'home-cooking',
    src: '/assets/home-showcase/home-cooking.webp',
    label: '美食摄影',
    prompt: '生成一张家常菜俯拍照：红烧肉、番茄炒蛋、清炒时蔬和一碗鸡汤，热气腾腾，写实摄影风格',
  },
  {
    id: 'astronaut-shiba',
    src: '/assets/home-showcase/astronaut-shiba.webp',
    label: '卡通头像',
    prompt: '画一个柴犬宇航员头像，戴透明头盔，背景是星空和地球，卡通风格',
  },
  {
    id: 'paris-bakery',
    src: '/assets/home-showcase/paris-bakery.webp',
    label: '街景摄影',
    prompt: '生成一张巴黎街头面包店的照片：红色复古自行车停在门口，车篮里插着法棍，阳光斜照',
  },
  {
    id: 'egret-watercolor',
    src: '/assets/home-showcase/egret-watercolor.webp',
    label: '水彩风景',
    prompt: '画一只清晨湖边的白鹭，薄雾和芦苇，水彩风格，留白多一些',
  },
  {
    id: 'lighthouse-sunset',
    src: '/assets/home-showcase/lighthouse-sunset.webp',
    label: '风光摄影',
    prompt: '生成一张海边灯塔的夕阳照片：海浪拍打礁石，天空是金橙色和粉紫色',
  },
];

// 后端灵感目录仍是真源；这组内容只负责保证 SSR 首帧有真实可用的入口，避免强刷后整块内容后插入。
const FALLBACK_INSPIRATIONS = [
  '帮我梳理今天最重要的三件事',
  '把一段材料整理成行动清单',
  '比较两个方案的成本和风险',
  '解释一个最近遇到的技术问题',
  '为下周制定一份执行计划',
  '检查这段文字的逻辑和表达',
  '从一份报告中提炼关键结论',
  '设计一套用户访谈问题',
  '把会议纪要整理成责任清单',
  '分析一组数据里的异常趋势',
  '规划一周的高效学习安排',
  '为发布公告调整表达和语气',
];

const SHOWCASE_PAGE_SIZE = 4;
const SHOWCASE_ROTATE_INTERVAL = 12_000;
const INSPIRATION_FETCH_LIMIT = 50;
const INSPIRATION_ROTATE_INTERVAL = 15_000;
const CAROUSEL_FLIP_STAGGER = 80;
const CAROUSEL_REPLACE_PADDING = 300;
const CAROUSEL_FLIP_BACK_DELAY = 50;
const INSPIRATION_ROW_COUNT = 2;
const INSPIRATION_GAP = 8;
const DEFAULT_INSPIRATION_WIDTH = 768;

function estimateInspirationWidth(question: string): number {
  const textWidth = Array.from(question).reduce((width, character) => (
    width + (/[^\u0000-\u00ff]/.test(character) ? 12 : 7)
  ), 0);
  return textWidth + 30;
}

function fitInspirationRows(
  pool: string[],
  startIndex: number,
  containerWidth: number,
  measuredWidths: Record<string, number>,
): string[] {
  if (pool.length === 0) return [];

  const visible: string[] = [];
  let row = 0;
  let rowWidth = 0;
  const availableWidth = Math.max(containerWidth, 1);

  for (let offset = 0; offset < pool.length; offset += 1) {
    const question = pool[(startIndex + offset) % pool.length];
    const chipWidth = Math.min(
      measuredWidths[question] ?? estimateInspirationWidth(question),
      availableWidth,
    );
    const nextWidth = rowWidth === 0
      ? chipWidth
      : rowWidth + INSPIRATION_GAP + chipWidth;

    if (rowWidth > 0 && nextWidth > availableWidth) {
      row += 1;
      if (row >= INSPIRATION_ROW_COUNT) break;
      rowWidth = chipWidth;
    } else {
      rowWidth = nextWidth;
    }
    visible.push(question);
  }

  return visible;
}

/**
 * 只展示用户用得上的灵感：需要某项功能的题，要等权益确认有该功能才展示，
 * 避免游客先看到生图、地图题又消失，或点了才发现用不了。
 */
function toInspirationPool(
  examples: PromptExample[],
  features: Entitlements['features'] | null,
): string[] {
  return Array.from(new Set(
    examples
      .filter((item) => !item.requires || features?.[item.requires] === true)
      .map((item) => item.question.trim())
      .filter(Boolean),
  ));
}

interface HomePageProps {
  onSelectPrompt: (content: string) => void;
  /** 当前用户的功能开关；权益未加载时为 null */
  features?: Entitlements['features'] | null;
}

const HomePage: React.FC<HomePageProps> = ({ onSelectPrompt, features = null }) => {
  useRenderProbe('HomePage');
  // 生图要等权益确认开通才展示，避免游客或未开通的用户点了才发现用不了。
  const canGenerateImages = features?.image_generation === true;
  const showcaseImages = useMemo(
    () => (canGenerateImages ? SHOWCASE_IMAGES : []),
    [canGenerateImages],
  );
  const [showcaseStartIndex, setShowcaseStartIndex] = useState(0);
  const [isShowcaseFlipped, setIsShowcaseFlipped] = useState(false);
  const [isShowcasePaused, setIsShowcasePaused] = useState(false);
  const [inspirationExamples, setInspirationExamples] = useState<PromptExample[] | null>(null);
  const inspirationPool = useMemo(() => {
    const pool = inspirationExamples ? toInspirationPool(inspirationExamples, features) : [];
    return pool.length > 0 ? pool : FALLBACK_INSPIRATIONS;
  }, [features, inspirationExamples]);
  const [inspirationStartIndex, setInspirationStartIndex] = useState(0);
  const [inspirationLayout, setInspirationLayout] = useState<{
    containerWidth: number;
    measuredWidths: Record<string, number>;
  }>({
    containerWidth: DEFAULT_INSPIRATION_WIDTH,
    measuredWidths: {},
  });
  const [areInspirationsFlipped, setAreInspirationsFlipped] = useState(false);
  const [isInspirationPaused, setIsInspirationPaused] = useState(false);
  const inspirationContainerRef = useRef<HTMLDivElement>(null);
  const inspirationMeasureRef = useRef<HTMLDivElement>(null);
  const showcaseFlipTimeoutsRef = useRef<Array<ReturnType<typeof setTimeout>>>([]);
  const flipTimeoutsRef = useRef<Array<ReturnType<typeof setTimeout>>>([]);
  const visibleShowcase = useMemo(() => {
    if (showcaseImages.length <= SHOWCASE_PAGE_SIZE) {
      return showcaseImages;
    }
    return Array.from(
      { length: SHOWCASE_PAGE_SIZE },
      (_, offset) => showcaseImages[(showcaseStartIndex + offset) % showcaseImages.length],
    );
  }, [showcaseImages, showcaseStartIndex]);
  const inspirations = useMemo(() => fitInspirationRows(
    inspirationPool,
    inspirationStartIndex,
    inspirationLayout.containerWidth,
    inspirationLayout.measuredWidths,
  ), [inspirationLayout, inspirationPool, inspirationStartIndex]);

  useEffect(() => {
    let cancelled = false;
    void preloadChatMessageList();

    void fetchPromptExamples(INSPIRATION_FETCH_LIMIT)
      .then((data) => {
        if (cancelled) return;
        if (data.examples.length > 0) {
          setInspirationExamples(data.examples);
          setInspirationStartIndex(0);
        }
      })
      .catch(() => {
        // 动态目录暂不可用时保留首帧兜底，避免灵感区域消失并引发布局跳动。
      });

    return () => {
      cancelled = true;
      showcaseFlipTimeoutsRef.current.forEach(clearTimeout);
      showcaseFlipTimeoutsRef.current = [];
      flipTimeoutsRef.current.forEach(clearTimeout);
      flipTimeoutsRef.current = [];
    };
  }, []);

  useEffect(() => {
    if (inspirationPool.length === 0) return undefined;

    const measure = () => {
      const containerWidth = inspirationContainerRef.current?.getBoundingClientRect().width
        || DEFAULT_INSPIRATION_WIDTH;
      const measureItems = Array.from(inspirationMeasureRef.current?.children ?? []);
      const measuredWidths = Object.fromEntries(inspirationPool.map((question, index) => {
        const measuredWidth = measureItems[index]?.getBoundingClientRect().width ?? 0;
        return [question, measuredWidth > 0 ? measuredWidth : estimateInspirationWidth(question)];
      }));
      setInspirationLayout((current) => {
        const sameWidth = Math.abs(current.containerWidth - containerWidth) < 1;
        const sameMeasurements = inspirationPool.every(
          (question) => current.measuredWidths[question] === measuredWidths[question],
        );
        return sameWidth && sameMeasurements
          ? current
          : { containerWidth, measuredWidths };
      });
    };

    measure();
    if (typeof ResizeObserver === 'undefined' || !inspirationContainerRef.current) {
      return undefined;
    }
    const observer = new ResizeObserver(measure);
    observer.observe(inspirationContainerRef.current);
    return () => observer.disconnect();
  }, [inspirationPool]);

  useEffect(() => {
    if (
      isInspirationPaused
      || inspirations.length === 0
      || inspirationPool.length <= inspirations.length
    ) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      const nextStartIndex = (inspirationStartIndex + inspirations.length) % inspirationPool.length;
      const nextInspirations = fitInspirationRows(
        inspirationPool,
        nextStartIndex,
        inspirationLayout.containerWidth,
        inspirationLayout.measuredWidths,
      );
      if (nextInspirations.length === 0) return;

      flipTimeoutsRef.current.forEach(clearTimeout);
      flipTimeoutsRef.current = [];
      setAreInspirationsFlipped(true);

      const replaceDelay = inspirations.length * CAROUSEL_FLIP_STAGGER
        + CAROUSEL_REPLACE_PADDING;
      flipTimeoutsRef.current.push(setTimeout(() => {
        setInspirationStartIndex(nextStartIndex);
        flipTimeoutsRef.current.push(setTimeout(() => {
          setAreInspirationsFlipped(false);
        }, CAROUSEL_FLIP_BACK_DELAY));
      }, replaceDelay));
    }, INSPIRATION_ROTATE_INTERVAL);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [
    inspirationLayout,
    inspirationPool,
    inspirationStartIndex,
    inspirations,
    isInspirationPaused,
  ]);

  const showNextShowcasePage = useCallback(() => {
    if (showcaseImages.length <= SHOWCASE_PAGE_SIZE) return;

    showcaseFlipTimeoutsRef.current.forEach(clearTimeout);
    showcaseFlipTimeoutsRef.current = [];
    setIsShowcaseFlipped(true);

    const replaceDelay = SHOWCASE_PAGE_SIZE * CAROUSEL_FLIP_STAGGER
      + CAROUSEL_REPLACE_PADDING;
    showcaseFlipTimeoutsRef.current.push(setTimeout(() => {
      setShowcaseStartIndex((current) => (
        (current + SHOWCASE_PAGE_SIZE) % showcaseImages.length
      ));
      showcaseFlipTimeoutsRef.current.push(setTimeout(() => {
        setIsShowcaseFlipped(false);
      }, CAROUSEL_FLIP_BACK_DELAY));
    }, replaceDelay));
  }, [showcaseImages.length]);

  useEffect(() => {
    if (isShowcasePaused || showcaseImages.length <= SHOWCASE_PAGE_SIZE) return undefined;
    const intervalId = window.setInterval(showNextShowcasePage, SHOWCASE_ROTATE_INTERVAL);
    return () => window.clearInterval(intervalId);
  }, [isShowcasePaused, showNextShowcasePage, showcaseImages.length]);

  return (
    <div className="flex min-h-full items-center justify-center px-4 pb-20 pt-8 sm:pb-24">
      <div className="mx-auto w-full max-w-3xl">
        <div className="mb-8 text-center">
          <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-border/70 bg-background/80 px-3 py-1 text-xs text-muted-foreground shadow-fdv2-xs">
            <Sparkles className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
            从一个任务开始，随时可以修改
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">
            今天想完成什么？
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">
            直接输入，或者从下面挑一个开始
          </p>
        </div>

        {visibleShowcase.length > 0 ? (
          <section aria-labelledby="image-showcase-title">
            <h2 id="image-showcase-title" className="sr-only">生图示例</h2>
            <div
              data-testid="image-showcase"
              className="grid grid-cols-2 gap-3 sm:grid-cols-4"
              style={{ contain: 'layout' }}
              onMouseEnter={() => setIsShowcasePaused(true)}
              onMouseLeave={() => setIsShowcasePaused(false)}
              onFocusCapture={() => setIsShowcasePaused(true)}
              onBlurCapture={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) {
                  setIsShowcasePaused(false);
                }
              }}
            >
              {visibleShowcase.map((image, index) => (
                <div
                  key={image.id}
                  data-testid="showcase-card"
                  style={{
                    backfaceVisibility: 'hidden',
                    opacity: isShowcaseFlipped ? 0 : 1,
                    transform: isShowcaseFlipped ? 'rotateX(90deg)' : 'rotateX(0deg)',
                    transition: 'opacity 300ms ease, transform 300ms ease',
                    transitionDelay: `${index * CAROUSEL_FLIP_STAGGER}ms`,
                    willChange: 'opacity, transform',
                  }}
                >
                  <button
                    type="button"
                    onClick={() => onSelectPrompt(image.prompt)}
                    aria-label={`做同款：${image.label}`}
                    className="group relative block aspect-[4/5] w-full overflow-hidden rounded-2xl border border-border/60 bg-muted shadow-fdv2-xs transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transform-none"
                  >
                    <Image
                      src={image.src}
                      alt={image.prompt}
                      fill
                      sizes="(min-width: 640px) 184px, 45vw"
                      className="object-cover transition-transform duration-500 group-hover:scale-[1.03] motion-reduce:transition-none"
                    />
                    <span className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-2 bg-gradient-to-t from-black/60 to-transparent px-3 pb-2.5 pt-8 text-left">
                      <span className="text-xs font-medium text-white">{image.label}</span>
                      <span className="flex items-center gap-1 text-[11px] text-white/85 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                        <Wand2 className="h-3 w-3" aria-hidden="true" />
                        做同款
                      </span>
                    </span>
                  </button>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        {inspirations.length > 0 ? (
          <section
            className="relative mt-6 border-t border-border/60 pt-4"
            aria-labelledby="daily-inspiration-title"
            onMouseEnter={() => setIsInspirationPaused(true)}
            onMouseLeave={() => setIsInspirationPaused(false)}
            onFocusCapture={() => setIsInspirationPaused(true)}
            onBlurCapture={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) {
                setIsInspirationPaused(false);
              }
            }}
          >
            <div className="mb-2 flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              <h2 id="daily-inspiration-title" className="font-medium">今日灵感</h2>
            </div>
            <div
              ref={inspirationMeasureRef}
              aria-hidden="true"
              className="pointer-events-none absolute left-0 top-0 flex h-0 w-0 gap-2 overflow-hidden invisible"
            >
              {inspirationPool.map((question) => (
                <span
                  key={question}
                  className="shrink-0 whitespace-nowrap rounded-full border px-3.5 py-2 text-xs"
                >
                  {question}
                </span>
              ))}
            </div>
            <div
              ref={inspirationContainerRef}
              data-testid="inspiration-cloud"
              data-row-count={INSPIRATION_ROW_COUNT}
              className="flex min-h-[4.75rem] flex-wrap content-center justify-center gap-2 overflow-hidden"
              style={{ contain: 'layout paint' }}
            >
              {inspirations.map((question, index) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => onSelectPrompt(question)}
                  onPointerMove={pointGlassLight}
                  onPointerLeave={resetGlassLight}
                  className={cn(
                    'max-w-full truncate whitespace-nowrap rounded-full px-3.5 py-2 text-xs text-fg-secondary hover:text-foreground',
                    glassSurface.surface,
                    glassSurface.pill,
                    glassSurface.interactive,
                  )}
                  style={{
                    backfaceVisibility: 'hidden',
                    opacity: areInspirationsFlipped ? 0 : 1,
                    transform: areInspirationsFlipped ? 'rotateX(90deg)' : 'rotateX(0deg)',
                    transition: 'opacity 300ms ease, transform 300ms ease',
                    transitionDelay: `${index * CAROUSEL_FLIP_STAGGER}ms`,
                    willChange: 'opacity, transform',
                  }}
                >
                  <GlassHoverLens corners={false} />
                  <span>{question}</span>
                </button>
              ))}
            </div>
          </section>
        ) : null}
      </div>

    </div>
  );
};

export default memo(HomePage);
