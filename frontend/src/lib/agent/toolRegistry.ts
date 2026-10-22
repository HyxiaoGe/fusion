/**
 * 工具元数据注册表（contract §8）。
 *
 * 加新工具只改这张表；组件代码不允许出现 if (toolName === '...') 这类硬编码。
 *
 * icon 用 Lucide component reference 而不是字符串——保证类型安全 + tree-shaking 友好。
 */

import { BookOpen, Clapperboard, CloudSun, FilePen, FilePenLine, Library, Search, Globe, Images, ImagePlus, MapPin, MapPinned, Plane, Route, Train, Wrench } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type SemanticColor = 'info' | 'success' | 'warn' | 'danger' | 'teal' | 'neutral';

interface ArgumentField {
  label: string;
  /** 枚举或布尔取值的显示文案；没列出的取值原样显示。 */
  values?: Record<string, string>;
}

const YES_NO = { true: '是', false: '否' };

interface ToolMeta {
  label: string;
  icon: LucideIcon;
  color: SemanticColor;
  /** arguments → 单行摘要文案（如 query 或 url） */
  summarize: (args: Record<string, unknown>) => string;
  /** 准备类工具（如加载技能说明）：完成后不算“有结果待分析”。 */
  preparatory?: boolean;
  /** 过程详情里参数的中文名；没列出的参数显示原名。 */
  arguments?: Record<string, ArgumentField>;
}

export const TOOL_REGISTRY: Record<string, ToolMeta> = {
  web_search: {
    label: '搜索',
    icon: Search,
    color: 'info',
    summarize: (a) => String(a.query ?? ''),
    arguments: {
      query: { label: '搜索词' },
      count: { label: '结果数' },
      intent: {
        label: '搜索目的',
        values: {
          quick_fact: '查事实',
          freshness: '最新动态',
          comparison: '对比',
          deep_research: '深入研究',
          official_source: '官方来源',
        },
      },
      domains: { label: '限定网站' },
      recency_days: { label: '时间范围（天）' },
    },
  },
  url_read: {
    label: '读取',
    icon: Globe,
    color: 'teal',
    summarize: (a) => String(a.url ?? ''),
    arguments: {
      url: { label: '网址' },
      reason: { label: '读取原因' },
      full_page: { label: '读取整页', values: YES_NO },
    },
  },
  knowledge_search: {
    label: '检索知识库',
    icon: Library,
    color: 'success',
    summarize: (a) => String(a.query ?? ''),
    arguments: {
      query: { label: '检索词' },
    },
  },
  local_place_search: {
    label: '搜索附近地点',
    icon: MapPin,
    color: 'teal',
    summarize: (a) => joinSummaryParts(
      firstStringArgument(a, ['location', 'area', 'city', 'near']),
      firstStringArgument(a, ['query', 'keywords', 'keyword']),
      '附近地点',
    ),
  },
  route_compare: {
    label: '比较路线',
    icon: Route,
    color: 'info',
    summarize: (a) => {
      const origin = firstStringArgument(a, ['origin', 'from', 'start']);
      const destination = firstStringArgument(a, ['destination', 'to', 'end']);
      if (origin && destination) return `${origin} → ${destination}`;
      return origin || destination || '路线方案';
    },
  },
  weather_forecast: {
    label: '查询天气',
    icon: CloudSun,
    color: 'info',
    summarize: summarizeWeatherQuery,
    arguments: {
      location: { label: '地点' },
      location_source: { label: '地点来源', values: { named: '指定地点', current_location: '当前位置' } },
      requested_date: { label: '日期' },
    },
  },
  search_flights: {
    label: '查询航班',
    icon: Plane,
    color: 'info',
    summarize: summarizeTravelQuery,
  },
  search_trains: {
    label: '查询高铁',
    icon: Train,
    color: 'teal',
    summarize: summarizeTravelQuery,
  },
  present_itinerary: {
    label: '整理行程方案',
    icon: MapPinned,
    color: 'info',
    summarize: (a) => firstStringArgument(a, ['title']),
    arguments: {
      title: { label: '标题' },
      plans: { label: '方案' },
    },
  },
  create_document: {
    label: '撰写文档',
    icon: FilePen,
    color: 'info',
    summarize: (a) => firstStringArgument(a, ['title']),
    arguments: {
      title: { label: '标题' },
      content: { label: '内容' },
    },
  },
  edit_document: {
    label: '修改文档',
    icon: FilePenLine,
    color: 'info',
    summarize: (a) => firstStringArgument(a, ['title']),
    arguments: {
      document_id: { label: '文档' },
      title: { label: '标题' },
      change_summary: { label: '修改说明' },
      edits: { label: '修改内容' },
    },
  },
  image_search: {
    label: '搜索图片',
    icon: Images,
    color: 'info',
    summarize: (a) => firstStringArgument(a, ['query']),
    arguments: {
      query: { label: '搜索词' },
      count: { label: '数量' },
    },
  },
  video_search: {
    label: '搜索视频',
    icon: Clapperboard,
    color: 'info',
    summarize: (a) => firstStringArgument(a, ['query']),
    arguments: {
      query: { label: '搜索词' },
      count: { label: '数量' },
    },
  },
  generate_image: {
    label: '生成图片',
    icon: ImagePlus,
    color: 'info',
    summarize: (a) => firstStringArgument(a, ['prompt']),
    arguments: {
      prompt: { label: '画面描述' },
      style: { label: '风格' },
      aspect_ratio: { label: '画面比例' },
      quality: { label: '画质', values: { standard: '标准', high: '高' } },
      reference_subject: { label: '参考对象' },
      regenerate: { label: '重新生成', values: YES_NO },
    },
  },
  load_skill: {
    label: '加载技能',
    icon: BookOpen,
    color: 'neutral',
    preparatory: true,
    summarize: (a) => joinSummaryParts(
      firstStringArgument(a, ['name']),
      firstStringArgument(a, ['file']),
      '技能说明',
    ),
    arguments: {
      name: { label: '技能' },
      file: { label: '文件' },
    },
  },
};

const FALLBACK: ToolMeta = {
  label: '外部工具',
  icon: Wrench,
  color: 'neutral',
  summarize: () => '',
};

export function getToolMeta(toolName: string): ToolMeta {
  const found = TOOL_REGISTRY[toolName];
  if (found) return found;
  return FALLBACK;
}

/** 过程详情里的参数名和取值：有中文名的换成中文，其余原样。 */
export function describeToolArgument(toolName: string, key: string, value: string): { label: string; value: string } {
  const field = getToolMeta(toolName).arguments?.[key];
  if (!field) return { label: key, value };
  return { label: field.label, value: field.values?.[value] ?? value };
}

export function hasToolMeta(toolName: string): boolean {
  return Object.prototype.hasOwnProperty.call(TOOL_REGISTRY, toolName);
}

function firstStringArgument(args: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = args[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

function joinSummaryParts(first: string, second: string, fallback: string): string {
  if (first && second) return `${first} · ${second}`;
  return first || second || fallback;
}

function summarizeTravelQuery(args: Record<string, unknown>): string {
  const origin = firstStringArgument(args, ['origin', 'from']);
  const destination = firstStringArgument(args, ['destination', 'to']);
  const date = firstStringArgument(args, ['departure_date', 'date']);
  const route = origin && destination ? `${origin} → ${destination}` : origin || destination;
  return joinSummaryParts(route, date, '出行方案');
}

function summarizeWeatherQuery(args: Record<string, unknown>): string {
  const location = firstStringArgument(args, ['location']);
  const legacyCity = firstStringArgument(args, ['city']);
  return location || legacyCity || '天气预报';
}
