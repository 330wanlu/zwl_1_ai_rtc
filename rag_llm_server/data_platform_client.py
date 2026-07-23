"""
数据中台（6-AI数据中台）HTTP 客户端。

封装 POST /api/v1/ai/query 与 GET /api/v1/ai/health。
超时或异常返回 None，由上层决定降级到方舟/纯文本。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DATA_PLATFORM_ENABLED = os.getenv("DATA_PLATFORM_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
DATA_PLATFORM_BASE_URL = os.getenv(
    "DATA_PLATFORM_BASE_URL", "http://127.0.0.1:8000"
).rstrip("/")
DATA_PLATFORM_API_KEY = os.getenv("DATA_PLATFORM_API_KEY", "").strip()
DATA_PLATFORM_TIMEOUT_MS = int(os.getenv("DATA_PLATFORM_TIMEOUT_MS", "1500"))
DATA_PLATFORM_LIMIT = int(os.getenv("DATA_PLATFORM_LIMIT", "3"))


@dataclass
class MatchedMedia:
    id: int | str
    type: str
    url: str
    name: str = ""
    caption: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MatchedMedia":
        return cls(
            id=raw.get("id", ""),
            type=str(raw.get("type") or ""),
            url=str(raw.get("url") or ""),
            name=str(raw.get("name") or ""),
            caption=raw.get("caption"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "url": self.url,
            "name": self.name,
            "caption": self.caption,
        }


@dataclass
class MatchedItem:
    knowledge_id: int
    title: str
    answer: str
    score: float = 0.0
    media: list[MatchedMedia] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MatchedItem":
        media_raw = raw.get("media") or []
        return cls(
            knowledge_id=int(raw.get("knowledge_id") or 0),
            title=str(raw.get("title") or ""),
            answer=str(raw.get("answer") or ""),
            score=float(raw.get("score") or 0.0),
            media=[MatchedMedia.from_dict(m) for m in media_raw if isinstance(m, dict)],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": self.knowledge_id,
            "title": self.title,
            "answer": self.answer,
            "score": self.score,
            "media": [m.to_dict() for m in self.media],
        }


@dataclass
class AiQueryResult:
    matched: bool
    items: list[MatchedItem] = field(default_factory=list)

    @property
    def top(self) -> MatchedItem | None:
        return self.items[0] if self.items else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "items": [i.to_dict() for i in self.items],
        }


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if DATA_PLATFORM_API_KEY:
        headers["X-API-Key"] = DATA_PLATFORM_API_KEY
    return headers


def _timeout() -> httpx.Timeout:
    seconds = max(DATA_PLATFORM_TIMEOUT_MS, 1) / 1000.0
    return httpx.Timeout(seconds)


async def health_check() -> dict[str, Any] | None:
    """探测中台 AI 服务。失败返回 None。"""
    url = f"{DATA_PLATFORM_BASE_URL}/api/v1/ai/health"
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.get(url, headers=_headers())
            if resp.status_code != 200:
                print(
                    f"\033[33m[DataPlatform] health HTTP {resp.status_code}: "
                    f"{resp.text[:200]}\033[0m"
                )
                return None
            body = resp.json()
            if body.get("code") != 0:
                print(f"\033[33m[DataPlatform] health code!=0: {body}\033[0m")
                return None
            return body.get("data") if isinstance(body.get("data"), dict) else body
    except Exception as e:
        print(f"\033[33m[DataPlatform] health failed: {e}\033[0m")
        return None


async def query_ai(
    query: str,
    *,
    limit: int | None = None,
    need_media: bool = True,
    intent: str | None = None,
) -> AiQueryResult | None:
    """
    调用中台多模态知识查询。

    返回:
      - AiQueryResult: 调用成功（含 matched=false）
      - None: 网络/超时/协议错误，上层应降级
    """
    text = (query or "").strip()
    if not text:
        return AiQueryResult(matched=False, items=[])

    payload: dict[str, Any] = {
        "query": text,
        "limit": limit if limit is not None else DATA_PLATFORM_LIMIT,
        "need_media": need_media,
    }
    if intent:
        payload["intent"] = intent

    url = f"{DATA_PLATFORM_BASE_URL}/api/v1/ai/query"
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(url, headers=_headers(), json=payload)
            if resp.status_code != 200:
                print(
                    f"\033[33m[DataPlatform] query HTTP {resp.status_code}: "
                    f"{resp.text[:200]}\033[0m"
                )
                return None
            body = resp.json()
    except Exception as e:
        print(f"\033[33m[DataPlatform] query failed: {e}\033[0m")
        return None

    if not isinstance(body, dict) or body.get("code") != 0:
        print(f"\033[33m[DataPlatform] query code!=0: {body}\033[0m")
        return None

    data = body.get("data") or {}
    if not isinstance(data, dict):
        print(f"\033[33m[DataPlatform] query invalid data: {data}\033[0m")
        return None

    items_raw = data.get("items") or []
    items = [MatchedItem.from_dict(x) for x in items_raw if isinstance(x, dict)]
    matched = bool(data.get("matched")) and len(items) > 0
    result = AiQueryResult(matched=matched, items=items)

    top = result.top
    media_n = len(top.media) if top else 0
    print(
        f"\033[36m[DataPlatform] matched={result.matched} "
        f"items={len(result.items)} media={media_n} "
        f"query={text[:40]!r}\033[0m"
    )
    return result
