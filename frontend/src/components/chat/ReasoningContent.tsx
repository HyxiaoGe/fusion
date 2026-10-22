'use client';

import { Brain, CheckCircle, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/utils';
import React, { useRef, useEffect, useState, useId } from 'react';
import { getToolMeta } from '@/lib/agent/toolRegistry';
import { countThinkingRounds, type ThinkingTraceEntry } from './thinkingTrace';
import { ReasoningMarkdown } from './ReasoningMarkdown';
import styles from './MessageAuxiliary.module.css';

interface ReasoningContentProps {
  content: string;
  /** 多轮思考与工具调用按发生顺序穿插；只有一轮且没调工具时按整段展示。 */
  trace?: readonly ThinkingTraceEntry[];
  isStreaming: boolean;
  isVisible: boolean;
  onToggle: () => void;
  duration?: string | null;
  startTime?: number;
  endTime?: number;
}

function ThinkingTraceList({ trace }: { trace: readonly ThinkingTraceEntry[] }) {
  return (
    <ol className={styles.trace}>
      {trace.map(entry => {
        if (entry.kind === 'thinking') {
          return (
            <li key={entry.id} className={styles.traceThinking}>
              <ReasoningMarkdown content={entry.text} />
            </li>
          );
        }
        const meta = getToolMeta(entry.toolName);
        const Icon = meta.icon;
        return (
          <li key={entry.id} className={styles.traceTool}>
            <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="shrink-0">{meta.label}</span>
            {entry.summary ? <span className="min-w-0 truncate">{entry.summary}</span> : null}
          </li>
        );
      })}
    </ol>
  );
}

const ReasoningContent: React.FC<ReasoningContentProps> = ({
  content,
  trace,
  isStreaming,
  isVisible,
  onToggle,
  duration,
  startTime,
  endTime,
}) => {
  const actuallyVisible = isStreaming || isVisible;
  const bodyId = useId();

  // 检测内容是否溢出（需要滚动）
  const scrollRef = useRef<HTMLDivElement>(null);
  const [isOverflowing, setIsOverflowing] = useState(false);

  useEffect(() => {
    if (scrollRef.current) {
      setIsOverflowing(scrollRef.current.scrollHeight > scrollRef.current.clientHeight);
    }
  }, [content, trace, actuallyVisible]);

  // 流式期间自动滚到底部
  useEffect(() => {
    if (isStreaming && actuallyVisible && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [content, trace, isStreaming, actuallyVisible]);

  const rounds = trace ? countThinkingRounds(trace) : 0;
  const showTrace = Boolean(trace && (rounds > 1 || trace.some(entry => entry.kind === 'tool')));

  if (!isStreaming && (!content || !content.trim())) {
    return null;
  }

  const durationText = (() => {
    if (duration) return `${duration} 秒`;
    if (startTime && endTime) {
      const diff = ((endTime - startTime) / 1000);
      if (diff < 0) return null;
      return `${diff.toFixed(1)} 秒`;
    }
    return null;
  })();
  return (
    <div className={cn(styles.reasoning, isStreaming && styles.streaming)}>
      {/* 推理入口 */}
      <button
        type="button"
        aria-expanded={actuallyVisible}
        aria-controls={bodyId}
        onClick={onToggle}
        className={styles.header}
      >
        <div className="flex min-w-0 items-center gap-2">
          {/* 与生成期状态栏共用图标徽标，思考中/已完成只换图标与色调。 */}
          <span className={styles.statusIcon} data-done={isStreaming ? undefined : 'true'} aria-hidden="true">
            {isStreaming
              ? <Brain className="h-3.5 w-3.5 animate-pulse motion-reduce:animate-none" />
              : <CheckCircle className="h-3.5 w-3.5" />}
          </span>
          <span>
            {isStreaming
              ? '正在深度思考...'
              : `已深度思考${rounds > 1 ? ` · ${rounds} 轮` : ''}${durationText ? `（用时 ${durationText}）` : ''}`
            }
          </span>
        </div>
        <span className={styles.chevron} aria-hidden="true">
          <ChevronUp className={cn(
            'h-3.5 w-3.5 transition-transform duration-200',
            !actuallyVisible && 'rotate-180',
          )} />
        </span>
      </button>

      {/* 内容区（grid-rows 自适应展开，避免 200px 硬截断） */}
      <div
        id={bodyId}
        aria-hidden={!actuallyVisible}
        inert={!actuallyVisible}
        className={cn(
          styles.details,
          actuallyVisible ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0"
        )}
      >
        <div className="overflow-hidden">
          <div className={styles.detailsInner}>
            <div
              ref={scrollRef}
              className={styles.content}
            >
              {showTrace && trace ? (
                <ThinkingTraceList trace={trace} />
              ) : content && content.trim() ? (
                <ReasoningMarkdown content={content} />
              ) : (
                <span className="text-muted-foreground animate-pulse motion-reduce:animate-none">AI 正在组织思路...</span>
              )}
            </div>
            {isOverflowing && (
              <div className={styles.fade} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ReasoningContent;
