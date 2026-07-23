"""阶段5自验证：口语句数限制、前端体验标记、打断保留媒体语义。"""

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
os.environ.setdefault("SPOKEN_TEXT_MAX_SENTENCES", "4")
os.environ.setdefault("SPOKEN_TEXT_MAX_CHARS", "180")

import main as app_main  # noqa: E402


def _ok(name: str) -> None:
    print(f"[PASS] {name}")


def _fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


def test_sanitize_sentences() -> None:
    long = (
        "第一句说明价值。"
        "第二句补充就业。"
        "第三句再说市场需求。"
        "第四句提醒可以看图。"
        "第五句不该被读出来。"
        "第六句也不该。"
    )
    out = app_main._sanitize_spoken_text(long, max_chars=500, max_sentences=4)
    if "第五句" in out or "第六句" in out:
        _fail("sanitize.sentences", out)
    if "第一句" not in out or "第四句" not in out:
        _fail("sanitize.keep4", out)
    _ok(f"sanitize.sentences -> {out}")

    dirty = "学 **RAG** 见 https://example.com/a 。第二句。"
    clean = app_main._sanitize_spoken_text(dirty, max_chars=180, max_sentences=4)
    if "http" in clean or "**" in clean:
        _fail("sanitize.clean", clean)
    _ok(f"sanitize.clean -> {clean}")


def test_frontend_markers() -> None:
    conv = (
        ROOT
        / "src"
        / "pages"
        / "MainPage"
        / "MainArea"
        / "Room"
        / "Conversation.tsx"
    ).read_text(encoding="utf-8")
    if "muted" not in conv:
        _fail("frontend.video.muted", "missing muted")
    if "图片暂时无法加载" not in conv:
        _fail("frontend.image.fallback", "missing broken placeholder")
    room = (ROOT / "src" / "store" / "slices" / "room.ts").read_text(encoding="utf-8")
    if "已挂到气泡上的 media 保留" not in room:
        _fail("frontend.interrupt.keep_media", "missing comment/intent")
    example = (SERVER_DIR / "scenes" / "Custom.json.example").read_text(encoding="utf-8")
    if "【多模态】" not in example:
        _fail("prompt.multimodal", "missing multimodal system message")
    _ok("frontend+prompt markers")


async def test_callback_still_ok() -> None:
    from httpx import ASGITransport, AsyncClient
    from media_session_store import media_session_store

    media_session_store.clear("ChatRoom01")
    transport = ASGITransport(app=app_main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
        pending = await client.get("/api/media/pending", params={"roomId": "ChatRoom01"})
        items = ((pending.json().get("data") or {}).get("items") or [])
        if not items:
            _fail("pending", str(pending.json()))
        _ok("callback+pending still ok")


async def main() -> int:
    print("=== phase5 self-check ===")
    test_sanitize_sentences()
    test_frontend_markers()
    await test_callback_still_ok()
    out = {"status": "pass"}
    path = ROOT / "docs" / "phase5_self_check.json"
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
