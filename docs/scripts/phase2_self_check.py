"""阶段2自验证：chat_callback 中台编排 + pending 侧通道 + 阶段0/1回归。

用法（需数据中台 :8000；本脚本用 ASGI 内存应用，不依赖已启动的 :3001）：
  cd rag_llm_server
  python ../docs/scripts/phase2_self_check.py

可选：再对已重启的 :3001 做一次 live 探测（LIVE_BASE=http://127.0.0.1:3001）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "rag_llm_server"
sys.path.insert(0, str(SERVER_DIR))

# 确保脚本进程启用中台（与 .env 一致；TestClient 会重新 import main）
os.environ.setdefault("DATA_PLATFORM_ENABLED", "true")
os.environ.setdefault("DATA_PLATFORM_BASE_URL", "http://127.0.0.1:8000")
os.environ.setdefault("DEFAULT_MEDIA_ROOM_ID", "ChatRoom01")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from data_platform_client import query_ai  # noqa: E402
from media_session_store import MediaSessionStore, media_session_store  # noqa: E402
import main as app_main  # noqa: E402


def _ok(name: str) -> None:
    print(f"[PASS] {name}")


def _fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


def _parse_sse_text(raw: str) -> tuple[str, bool]:
    """从 SSE 文本拼接 assistant content，并检查是否含 [DONE]。"""
    parts: list[str] = []
    done = False
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts), done


def _assert_no_media_url(text: str, media_urls: list[str]) -> None:
    for url in media_urls:
        if url and url in text:
            _fail("sse.no_media_url", f"SSE text contains media url: {url}")
    if "http://" in text or "https://" in text:
        _fail("sse.no_url", f"SSE text contains URL: {text[:120]}")


async def test_sanitize() -> None:
    text = app_main._sanitize_spoken_text(
        "学 **RAG** 有用。详见 https://example.com/a 和 [链接](http://x)。"
    )
    if "http" in text or "**" in text or "[" in text:
        _fail("sanitize", f"dirty text left: {text}")
    _ok(f"sanitize -> {text[:40]!r}")


async def test_callback_and_pending() -> dict:
    # 清空默认房间，避免脏数据
    media_session_store.clear("ChatRoom01")

    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 命中：学RAG
        resp = await client.post(
            "/api/chat_callback",
            json={
                "model": "test-model",
                "messages": [
                    {"role": "system", "content": "你是助手"},
                    {"role": "user", "content": "学RAG有什么用"},
                ],
                "stream": True,
                "roomId": "ChatRoom01",
            },
            timeout=30.0,
        )
        if resp.status_code != 200:
            _fail("callback.hit.status", f"{resp.status_code} {resp.text[:200]}")
        spoken, done = _parse_sse_text(resp.text)
        if not spoken:
            _fail("callback.hit.spoken", "empty SSE text")
        if not done:
            _fail("callback.hit.done", "missing [DONE]")
        if "假回复" in spoken:
            _fail("callback.hit.source", "fell back to fake reply")
        _ok(f"callback.hit.spoken len={len(spoken)}")

        pending = await client.get("/api/media/pending", params={"roomId": "ChatRoom01"})
        body = pending.json()
        items = (body.get("data") or {}).get("items") or []
        if pending.status_code != 200 or body.get("code") != 0:
            _fail("pending.hit", str(body))
        if len(items) != 1:
            _fail("pending.hit.count", f"items={len(items)} expect=1")
        media = items[0].get("media") or []
        if len(media) < 2:
            _fail("pending.hit.media", f"media={len(media)} expect>=2")
        urls = [m.get("url", "") for m in media]
        _assert_no_media_url(spoken, urls)
        _ok(f"pending.hit media={len(media)}")

        # 二次 pending 应为空
        pending2 = await client.get("/api/media/pending", params={"roomId": "ChatRoom01"})
        items2 = ((pending2.json().get("data") or {}).get("items") or [])
        if items2:
            _fail("pending.second_empty", str(items2))
        _ok("pending.second_empty")

        # 未命中：应不进 pending（可能走方舟，耗时更长）
        media_session_store.clear("ChatRoom01")
        resp_miss = await client.post(
            "/api/chat_callback",
            json={
                "model": "test-model",
                "messages": [{"role": "user", "content": "今天天气怎么样xyz_unique_miss"}],
                "stream": True,
                "roomId": "ChatRoom01",
            },
            timeout=60.0,
        )
        if resp_miss.status_code != 200:
            _fail("callback.miss.status", f"{resp_miss.status_code}")
        spoken_miss, done_miss = _parse_sse_text(resp_miss.text)
        if not done_miss:
            _fail("callback.miss.done", "missing [DONE]")
        # 未命中不应写入中台媒体
        pending_miss = await client.get(
            "/api/media/pending", params={"roomId": "ChatRoom01"}
        )
        items_miss = ((pending_miss.json().get("data") or {}).get("items") or [])
        if items_miss:
            _fail("callback.miss.no_media", str(items_miss))
        _ok(f"callback.miss no_media spoken_len={len(spoken_miss)}")

        return {
            "hit_spoken": spoken,
            "hit_media": media,
            "miss_spoken_preview": spoken_miss[:80],
        }


async def test_phase0_1_regression() -> None:
    # 阶段1 client
    r = await query_ai("怎么退货", need_media=True)
    if r is None or not r.matched or not r.top or len(r.top.media) < 2:
        _fail("phase1.regression.return", str(r))
    _ok("phase1.regression 怎么退货")

    r2 = await query_ai("学RAG有什么用", need_media=True)
    if r2 is None or not r2.matched or len(r2.top.media) < 2:
        _fail("phase1.regression.rag", str(r2))
    _ok("phase1.regression 学RAG有什么用")

    # 阶段1 store
    store = MediaSessionStore(ttl_sec=60, max_per_room=3)
    store.push(
        "r",
        query="q",
        title="t",
        answer="a",
        media=[{"id": 1, "type": "image", "url": "http://x/a.jpg"}],
    )
    assert len(store.pop_all("r")) == 1
    assert store.pop_all("r") == []
    _ok("phase1.regression store")


async def test_live_optional() -> None:
    live = os.getenv("LIVE_BASE", "").rstrip("/")
    if not live:
        print("[SKIP] live :3001（设置 LIVE_BASE=http://127.0.0.1:3001 可启用）")
        return
    async with AsyncClient(base_url=live, timeout=30.0) as client:
        resp = await client.post(
            "/api/chat_callback",
            json={
                "messages": [{"role": "user", "content": "怎么退货"}],
                "stream": True,
                "roomId": "ChatRoom01",
            },
        )
        spoken, done = _parse_sse_text(resp.text)
        if not done or not spoken:
            _fail("live.callback", f"status={resp.status_code} spoken={spoken!r}")
        pending = await client.get("/api/media/pending", params={"roomId": "ChatRoom01"})
        items = ((pending.json().get("data") or {}).get("items") or [])
        if len(items) < 1:
            _fail("live.pending", str(pending.json()))
        _ok(f"live {live} callback+pending")


async def main() -> int:
    print("=== phase2 self-check ===")
    print(f"DATA_PLATFORM_ENABLED={app_main.DATA_PLATFORM_ENABLED}")
    if not app_main.DATA_PLATFORM_ENABLED:
        _fail("env", "DATA_PLATFORM_ENABLED is false in loaded app")

    await test_sanitize()
    await test_phase0_1_regression()
    detail = await test_callback_and_pending()
    await test_live_optional()

    out = {"status": "pass", "detail": detail}
    out_path = ROOT / "docs" / "phase2_self_check.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print("=== ALL PASS ===")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except AssertionError as e:
        print(f"=== FAILED: {e} ===")
        raise SystemExit(1)
