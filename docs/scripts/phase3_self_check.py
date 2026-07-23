"""阶段3自验证：/api/media/health + pending 回归 + 阶段2链路。

用法：
  cd rag_llm_server
  python ../docs/scripts/phase3_self_check.py
  # 可选 live
  $env:LIVE_BASE='http://127.0.0.1:3001'; python ../docs/scripts/phase3_self_check.py
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


def _ok(name: str) -> None:
    print(f"[PASS] {name}")


def _fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


async def main() -> int:
    print("=== phase3 self-check ===")
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/media/health")
        body = health.json()
        if health.status_code != 200 or body.get("code") != 0:
            _fail("health", str(body))
        data = body.get("data") or {}
        if data.get("status") != "ok":
            _fail("health.status", str(data))
        if not data.get("data_platform_enabled"):
            _fail("health.enabled", str(data))
        if data.get("data_platform_ok") is not True:
            _fail("health.dp_ok", str(data))
        _ok("GET /api/media/health")

        media_session_store.clear("ChatRoom01")
        cb = await client.post(
            "/api/chat_callback",
            json={
                "messages": [{"role": "user", "content": "怎么退货"}],
                "stream": True,
                "roomId": "ChatRoom01",
            },
            timeout=30,
        )
        if cb.status_code != 200 or "[DONE]" not in cb.text:
            _fail("callback", f"{cb.status_code}")
        pending = await client.get("/api/media/pending", params={"roomId": "ChatRoom01"})
        items = ((pending.json().get("data") or {}).get("items") or [])
        if len(items) < 1 or len(items[0].get("media") or []) < 2:
            _fail("pending", str(pending.json()))
        _ok("callback+pending regression")

    live = os.getenv("LIVE_BASE", "").rstrip("/")
    if live:
        async with AsyncClient(base_url=live, timeout=20) as client:
            h = await client.get("/api/media/health")
            if h.status_code != 200 or h.json().get("code") != 0:
                _fail("live.health", h.text[:200])
            _ok(f"live health {live}")
    else:
        print("[SKIP] live health（设置 LIVE_BASE 可启用）")

    out = {"status": "pass", "health": data}
    path = ROOT / "docs" / "phase3_self_check.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    print("=== ALL PASS ===")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except AssertionError as e:
        print(f"=== FAILED: {e} ===")
        raise SystemExit(1)
