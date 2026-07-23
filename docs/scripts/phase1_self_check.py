"""阶段1自验证：data_platform_client + media_session_store + 阶段0回归。

用法（需数据中台 :8000 已启动）：
  cd rag_llm_server
  python ../docs/scripts/phase1_self_check.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = ROOT / "rag_llm_server"
sys.path.insert(0, str(SERVER_DIR))

from data_platform_client import (  # noqa: E402
    DATA_PLATFORM_BASE_URL,
    health_check,
    query_ai,
)
from media_session_store import MediaSessionStore  # noqa: E402


def _ok(name: str) -> None:
    print(f"[PASS] {name}")


def _fail(name: str, detail: str) -> None:
    print(f"[FAIL] {name}: {detail}")
    raise AssertionError(f"{name}: {detail}")


def test_media_store() -> None:
    store = MediaSessionStore(ttl_sec=2, max_per_room=2)
    room = "ChatRoom01"

    # empty pop
    assert store.pop_all(room) == []
    _ok("store.pop_all empty")

    # push without media -> None
    assert (
        store.push(room, query="q", title="t", answer="a", media=[]) is None
    )
    _ok("store.push skip empty media")

    item1 = store.push(
        room,
        query="怎么退货",
        title="退货流程",
        answer="请按图示步骤操作",
        media=[{"id": 1, "type": "image", "url": "http://x/a.jpg", "name": "a"}],
        knowledge_id=1,
        score=1.0,
    )
    assert item1 is not None
    item2 = store.push(
        room,
        query="学RAG有什么用",
        title="RAG",
        answer="有用",
        media=[
            {"id": 2, "type": "image", "url": "http://x/b.jpg", "name": "b"},
            {"id": 3, "type": "video", "url": "http://x/c.mp4", "name": "c"},
        ],
        knowledge_id=2,
        score=2.0,
    )
    assert item2 is not None
    # max_per_room=2，再 push 应丢最旧
    item3 = store.push(
        room,
        query="q3",
        title="t3",
        answer="a3",
        media=[{"id": 4, "type": "image", "url": "http://x/d.jpg"}],
    )
    peeked = store.peek(room)
    assert len(peeked) == 2
    assert peeked[0]["id"] == item2.id
    assert peeked[1]["id"] == item3.id
    _ok("store.max_per_room drop oldest")

    popped = store.pop_all(room)
    assert len(popped) == 2
    assert store.pop_all(room) == []
    _ok("store.pop_all consume once")

    # TTL
    store.push(
        room,
        query="ttl",
        title="ttl",
        answer="ttl",
        media=[{"id": 9, "type": "image", "url": "http://x/ttl.jpg"}],
    )
    time.sleep(2.2)
    assert store.pop_all(room) == []
    _ok("store.ttl expire")


async def test_client_and_phase0() -> dict:
    health = await health_check()
    if health is None:
        _fail("client.health", f"unreachable base={DATA_PLATFORM_BASE_URL}")
    _ok(f"client.health base={DATA_PLATFORM_BASE_URL}")

    cases = [
        ("怎么退货", True, 2),
        ("学RAG有什么用", True, 2),
        ("今天天气怎么样", False, 0),
    ]
    results = {}
    store = MediaSessionStore(ttl_sec=120, max_per_room=5)

    for query, expect_matched, expect_media_min in cases:
        result = await query_ai(query, need_media=True, limit=3)
        if result is None:
            _fail(f"client.query:{query}", "returned None")
        assert result is not None
        results[query] = result.to_dict()

        if result.matched != expect_matched:
            _fail(
                f"client.query:{query}",
                f"matched={result.matched} expect={expect_matched}",
            )

        if expect_matched:
            top = result.top
            assert top is not None
            if len(top.media) < expect_media_min:
                _fail(
                    f"client.query:{query}",
                    f"media={len(top.media)} expect>={expect_media_min}",
                )
            # 联通：命中结果可 push 到 store
            pushed = store.push(
                "ChatRoom01",
                query=query,
                title=top.title,
                answer=top.answer,
                media=[m.to_dict() for m in top.media],
                knowledge_id=top.knowledge_id,
                score=top.score,
            )
            assert pushed is not None
        _ok(f"client.query:{query} matched={result.matched}")

    pending = store.pop_all("ChatRoom01")
    # 两个命中问句各 push 一次
    if len(pending) != 2:
        _fail("client+store integration", f"pending={len(pending)} expect=2")
    _ok("client+store integration pop=2")

    # 二次 pop 为空
    if store.pop_all("ChatRoom01"):
        _fail("store.idempotent pop", "second pop not empty")
    _ok("store.idempotent second pop empty")

    return results


async def main() -> int:
    print("=== phase1 self-check ===")
    test_media_store()
    query_results = await test_client_and_phase0()

    out = {
        "data_platform_base_url": DATA_PLATFORM_BASE_URL,
        "query_results": query_results,
        "status": "pass",
    }
    out_path = ROOT / "docs" / "phase1_self_check.json"
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
