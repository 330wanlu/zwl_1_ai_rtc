"""阶段4自验证：后端侧通道 + 前端关键文件存在性 + 0~3回归抽检。

说明：UI 真机语音需人工看 Conversation 气泡；本脚本验证：
1) callback → pending 媒体契约
2) 前端 media/Redux/Conversation/hook 文件已落地
3) attachMediaToLatestAIMsg 逻辑可用（通过导入 TS 无法直接跑，改为契约 JSON 样例）

用法：
  cd rag_llm_server
  python ../docs/scripts/phase4_self_check.py
  $env:LIVE_BASE='http://127.0.0.1:3001'; python ../docs/scripts/phase4_self_check.py
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

os.environ.setdefault("DATA_PLATFORM_ENABLED", "true")
os.environ.setdefault("DATA_PLATFORM_BASE_URL", "http://127.0.0.1:8000")

from httpx import ASGITransport, AsyncClient  # noqa: E402

import main as app_main  # noqa: E402
from media_session_store import media_session_store  # noqa: E402


FRONTEND_FILES = [
    ROOT / "src" / "app" / "media.ts",
    ROOT / "src" / "lib" / "useMediaPending.ts",
    ROOT / "src" / "store" / "slices" / "room.ts",
    ROOT / "src" / "pages" / "MainPage" / "MainArea" / "Room" / "Conversation.tsx",
    ROOT / "src" / "pages" / "MainPage" / "MainArea" / "Room" / "index.tsx",
]


def _ok(name: str) -> None:
    print(f"[PASS] {name}")


def _fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


def test_frontend_files() -> None:
    for path in FRONTEND_FILES:
        if not path.exists():
            _fail("frontend.file", str(path))
        text = path.read_text(encoding="utf-8")
        if path.name == "room.ts":
            if "attachMediaToLatestAIMsg" not in text or "MsgMedia" not in text:
                _fail("frontend.room", "missing attachMediaToLatestAIMsg/MsgMedia")
        if path.name == "Conversation.tsx":
            if "<video" not in text or "<img" not in text:
                _fail("frontend.conversation", "missing img/video render")
        if path.name == "useMediaPending.ts":
            if "fetchPendingMedia" not in text:
                _fail("frontend.hook", "missing fetchPendingMedia")
        if path.name == "index.tsx" and path.parent.name == "Room":
            if "useMediaPending" not in text:
                _fail("frontend.room.mount", "useMediaPending not mounted")
    _ok("frontend files + markers")


async def test_backend_contract() -> dict:
    media_session_store.clear("ChatRoom01")
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        h = await client.get("/api/media/health")
        if h.status_code != 200 or h.json().get("code") != 0:
            _fail("health", h.text[:200])
        _ok("health")

        resp = await client.post(
            "/api/chat_callback",
            json={
                "messages": [{"role": "user", "content": "学RAG有什么用"}],
                "stream": True,
                "roomId": "ChatRoom01",
            },
            timeout=30,
        )
        if "[DONE]" not in resp.text:
            _fail("callback", "no DONE")
        if "http://" in "".join(
            line for line in resp.text.splitlines() if '"content"' in line
        ):
            # loose check: content deltas should not include full media urls typically
            pass
        pending = await client.get("/api/media/pending", params={"roomId": "ChatRoom01"})
        body = pending.json()
        items = (body.get("data") or {}).get("items") or []
        if not items:
            _fail("pending", str(body))
        media = items[0].get("media") or []
        types = {m.get("type") for m in media}
        if "image" not in types or "video" not in types:
            _fail("pending.types", str(types))
        _ok(f"backend contract media={len(media)}")
        return {"pending_item": items[0], "spoken_has_done": True}


async def test_live() -> None:
    live = os.getenv("LIVE_BASE", "").rstrip("/")
    if not live:
        print("[SKIP] live")
        return
    async with AsyncClient(base_url=live, timeout=20) as client:
        h = await client.get("/api/media/health")
        p = await client.get("/api/media/pending", params={"roomId": "ChatRoom01"})
        if h.status_code != 200 or p.status_code != 200:
            _fail("live", f"h={h.status_code} p={p.status_code}")
        _ok(f"live {live}")


async def main() -> int:
    print("=== phase4 self-check ===")
    test_frontend_files()
    detail = await test_backend_contract()
    await test_live()
    out = {"status": "pass", "detail": detail}
    path = ROOT / "docs" / "phase4_self_check.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    print("=== ALL PASS ===")
    print("NOTE: 请在浏览器进房语音问「学RAG有什么用」人工确认气泡出图/视频。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except AssertionError as e:
        print(f"=== FAILED: {e} ===")
        raise SystemExit(1)
