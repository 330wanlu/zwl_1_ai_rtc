"""
媒体会话暂存：按 room_id 缓存待前端拉取的多模态媒体。

规则（MVP）：
- push：追加一条命中结果（含 media）
- pop_all：弹出并清空该房间队列（避免重复渲染）
- TTL：过期条目在 push/pop 时清理
- 单房间队列上限：超出丢弃最旧
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MEDIA_ROOM_ID = os.getenv("DEFAULT_MEDIA_ROOM_ID", "ChatRoom01").strip() or "ChatRoom01"
MEDIA_PENDING_TTL_SEC = int(os.getenv("MEDIA_PENDING_TTL_SEC", "120"))
MEDIA_PENDING_MAX_PER_ROOM = int(os.getenv("MEDIA_PENDING_MAX_PER_ROOM", "5"))


@dataclass
class PendingMediaItem:
    id: str
    created_at: float
    query: str
    title: str
    answer: str
    media: list[dict[str, Any]] = field(default_factory=list)
    knowledge_id: int | None = None
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "query": self.query,
            "title": self.title,
            "answer": self.answer,
            "media": self.media,
            "knowledge_id": self.knowledge_id,
            "score": self.score,
        }


class MediaSessionStore:
    """进程内媒体暂存（线程安全）。"""

    def __init__(
        self,
        *,
        ttl_sec: int = MEDIA_PENDING_TTL_SEC,
        max_per_room: int = MEDIA_PENDING_MAX_PER_ROOM,
    ) -> None:
        self._ttl_sec = max(ttl_sec, 1)
        self._max_per_room = max(max_per_room, 1)
        self._lock = threading.Lock()
        self._rooms: dict[str, list[PendingMediaItem]] = {}

    def push(
        self,
        room_id: str,
        *,
        query: str,
        title: str,
        answer: str,
        media: list[dict[str, Any]],
        knowledge_id: int | None = None,
        score: float | None = None,
    ) -> PendingMediaItem | None:
        """写入一条待展示媒体。media 为空则跳过，返回 None。"""
        rid = (room_id or DEFAULT_MEDIA_ROOM_ID).strip() or DEFAULT_MEDIA_ROOM_ID
        cleaned_media = [m for m in (media or []) if isinstance(m, dict) and m.get("url")]
        if not cleaned_media:
            return None

        item = PendingMediaItem(
            id=str(uuid.uuid4()),
            created_at=time.time(),
            query=(query or "").strip(),
            title=(title or "").strip(),
            answer=(answer or "").strip(),
            media=cleaned_media,
            knowledge_id=knowledge_id,
            score=score,
        )

        with self._lock:
            self._purge_locked(rid)
            queue = self._rooms.setdefault(rid, [])
            queue.append(item)
            while len(queue) > self._max_per_room:
                queue.pop(0)
            print(
                f"\033[36m[MediaStore] push room={rid} id={item.id} "
                f"media={len(cleaned_media)} queue={len(queue)}\033[0m"
            )
            return item

    def pop_all(self, room_id: str) -> list[dict[str, Any]]:
        """弹出并清空该房间全部未过期条目。"""
        rid = (room_id or DEFAULT_MEDIA_ROOM_ID).strip() or DEFAULT_MEDIA_ROOM_ID
        with self._lock:
            self._purge_locked(rid)
            queue = self._rooms.pop(rid, [])
            items = [x.to_dict() for x in queue]
            print(
                f"\033[36m[MediaStore] pop_all room={rid} count={len(items)}\033[0m"
            )
            return items

    def peek(self, room_id: str) -> list[dict[str, Any]]:
        """只读查看（不消费），主要用于调试。"""
        rid = (room_id or DEFAULT_MEDIA_ROOM_ID).strip() or DEFAULT_MEDIA_ROOM_ID
        with self._lock:
            self._purge_locked(rid)
            return [x.to_dict() for x in self._rooms.get(rid, [])]

    def clear(self, room_id: str | None = None) -> None:
        """清空指定房间或全部。"""
        with self._lock:
            if room_id is None:
                self._rooms.clear()
                return
            rid = room_id.strip() or DEFAULT_MEDIA_ROOM_ID
            self._rooms.pop(rid, None)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            rooms = {}
            for rid, queue in self._rooms.items():
                alive = [x for x in queue if now - x.created_at <= self._ttl_sec]
                rooms[rid] = len(alive)
            return {
                "ttl_sec": self._ttl_sec,
                "max_per_room": self._max_per_room,
                "rooms": rooms,
            }

    def _purge_locked(self, room_id: str) -> None:
        queue = self._rooms.get(room_id)
        if not queue:
            return
        now = time.time()
        kept = [x for x in queue if now - x.created_at <= self._ttl_sec]
        if kept:
            self._rooms[room_id] = kept
        else:
            self._rooms.pop(room_id, None)


# 进程级单例，供后续 chat_callback / pending API 共用
media_session_store = MediaSessionStore()
