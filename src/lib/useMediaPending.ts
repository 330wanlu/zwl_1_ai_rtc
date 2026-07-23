/**
 * 进房且 Agent 启用后，轮询 /api/media/pending，把图/视频挂到最近 AI 气泡。
 */

import { useEffect, useRef } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { RootState } from '@/store';
import { attachMediaToLatestAIMsg } from '@/store/slices/room';
import { fetchPendingMedia } from '@/app/media';
import logger from '@/utils/logger';

const DEFAULT_INTERVAL_MS = 1000;

export function useMediaPending(intervalMs: number = DEFAULT_INTERVAL_MS) {
  const dispatch = useDispatch();
  const { isJoined, isAIGCEnable, roomId } = useSelector((state: RootState) => state.room);
  const inflight = useRef(false);

  useEffect(() => {
    if (!isJoined || !isAIGCEnable || !roomId) {
      return undefined;
    }

    let stopped = false;

    const tick = async () => {
      if (stopped || inflight.current) {
        return;
      }
      inflight.current = true;
      try {
        const items = await fetchPendingMedia(roomId);
        for (const item of items) {
          const media = (item.media || []).filter((m) => m?.url);
          if (!media.length) {
            continue;
          }
          dispatch(
            attachMediaToLatestAIMsg({
              media,
              mediaTitle: item.title,
              answer: item.answer,
            })
          );
        }
      } catch (e) {
        logger.debug('[useMediaPending] poll failed', e);
      } finally {
        inflight.current = false;
      }
    };

    tick();
    const timer = window.setInterval(tick, Math.max(intervalMs, 500));
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [dispatch, intervalMs, isAIGCEnable, isJoined, roomId]);
}

export default useMediaPending;
