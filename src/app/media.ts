/**
 * 多模态媒体侧通道 API（rag_llm_server）
 */

import { AIGC_PROXY_HOST } from '@/config';
import type { MsgMedia } from '@/store/slices/room';

export interface PendingMediaItem {
  id: string;
  created_at?: number;
  query?: string;
  title?: string;
  answer?: string;
  knowledge_id?: number | null;
  score?: number | null;
  media: MsgMedia[];
}

interface PendingResponse {
  code: number;
  message?: string;
  data?: {
    items?: PendingMediaItem[];
  };
}

/**
 * 拉取并清空房间待展示媒体。
 */
export async function fetchPendingMedia(roomId: string): Promise<PendingMediaItem[]> {
  if (!roomId) {
    return [];
  }
  const url = `${AIGC_PROXY_HOST}/api/media/pending?roomId=${encodeURIComponent(roomId)}`;
  const res = await fetch(url, {
    method: 'GET',
    headers: {
      Accept: 'application/json',
    },
  });
  if (!res.ok) {
    throw new Error(`pending media HTTP ${res.status}`);
  }
  const body = (await res.json()) as PendingResponse;
  if (body.code !== 0) {
    throw new Error(body.message || 'pending media failed');
  }
  return body.data?.items || [];
}

/**
 * 探活（可选，调试用）
 */
export async function fetchMediaHealth(): Promise<Record<string, unknown> | null> {
  const res = await fetch(`${AIGC_PROXY_HOST}/api/media/health`);
  if (!res.ok) {
    return null;
  }
  const body = await res.json();
  return body?.data ?? null;
}
