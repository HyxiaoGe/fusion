import React, { Suspense, lazy } from 'react';
import ChatLoadingSurface from '@/components/chat/ChatLoadingSurface';
import {
  preloadChatMessageList,
} from './preloaders';

// 懒加载组件定义
const LazyChatMessageList = lazy(preloadChatMessageList);

// 通用懒加载包装器组件
interface LazyWrapperProps {
  children: React.ReactNode;
  fallback?: React.ReactNode;
  className?: string;
}

export const LazyWrapper: React.FC<LazyWrapperProps> = ({ 
  children, 
  fallback,
  className 
}) => {
  const defaultFallback = (
    <ChatLoadingSurface className={className} />
  );

  return (
    <Suspense fallback={fallback || defaultFallback}>
      {children}
    </Suspense>
  );
};

// 具体的懒加载组件包装器
export const ChatMessageListLazy: React.FC<any> = ({ fallback, ...props }) => (
  <LazyWrapper fallback={fallback}>
    <LazyChatMessageList {...props} />
  </LazyWrapper>
);
