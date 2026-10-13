import { API_CONFIG } from '../config';
import { apiRequest } from './fetchWithAuth';
import { ApiError } from '@/types/api';

const API_BASE_URL = API_CONFIG.BASE_URL;

interface StreamStatusData {
  status: 'streaming' | 'done' | 'error' | 'not_found';
  last_entry_id?: string;
  message_id?: string;
  task_id?: string;
  stream_mode?: 'initial' | 'retry' | 'continuation';
}

export class StreamStatusRequestError extends Error {
  readonly recoverable: boolean;
  readonly code?: string;

  constructor(message: string, options: { recoverable: boolean; code?: string; cause?: unknown }) {
    super(message);
    this.name = 'StreamStatusRequestError';
    this.recoverable = options.recoverable;
    this.code = options.code;
    if (options.cause !== undefined) {
      Object.defineProperty(this, 'cause', { configurable: true, value: options.cause });
    }
  }
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: string }).name === 'AbortError'
  );
}

export async function fetchStreamStatus(
  conversationId: string,
  signal?: AbortSignal,
): Promise<StreamStatusData> {
  try {
    const url = `${API_BASE_URL}/api/chat/stream-status/${conversationId}`;
    return await (signal
      ? apiRequest<StreamStatusData>(url, { signal })
      : apiRequest<StreamStatusData>(url));
  } catch (error) {
    if (isAbortError(error)) throw error;
    const code = error instanceof ApiError ? error.code : undefined;
    const unauthorized = code === 'UNAUTHORIZED' || code === 'FORBIDDEN' ||
      (error instanceof Error && error.message === 'Unauthorized');
    throw new StreamStatusRequestError(
      error instanceof Error ? error.message : '流状态查询失败',
      {
        recoverable: !unauthorized && code !== 'NOT_FOUND',
        code,
        cause: error,
      },
    );
  }
}
