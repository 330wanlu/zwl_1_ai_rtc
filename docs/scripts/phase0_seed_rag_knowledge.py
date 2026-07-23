"""阶段0：向数据中台灌入「学 RAG 有什么用」演示知识 + 占位图/视频。

用法（需数据中台已启动）：
  python docs/scripts/phase0_seed_rag_knowledge.py

可选环境变量：
  DATA_PLATFORM_BASE_URL  默认 http://127.0.0.1:8000
  DATA_PLATFORM_API_KEY   若中台开启鉴权则填写
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.getenv("DATA_PLATFORM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
API_KEY = os.getenv("DATA_PLATFORM_API_KEY", "")


def _headers(extra: dict | None = None) -> dict:
    h = dict(extra or {})
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _request(method: str, path: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers=_headers(headers),
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {err}") from e


def _post_json(path: str, payload: dict):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request(
        "POST",
        path,
        data=raw,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def _get(path: str):
    return _request("GET", path)


def _write_placeholder_image(path: Path) -> None:
    jpeg = bytes(
        [
            0xFF,
            0xD8,
            0xFF,
            0xE0,
            0x00,
            0x10,
            0x4A,
            0x46,
            0x49,
            0x46,
            0x00,
            0x01,
            0x01,
            0x00,
            0x00,
            0x01,
            0x00,
            0x01,
            0x00,
            0x00,
            0xFF,
            0xDB,
            0x00,
            0x43,
            0x00,
        ]
        + [0x08] * 64
        + [
            0xFF,
            0xC0,
            0x00,
            0x0B,
            0x08,
            0x00,
            0x01,
            0x00,
            0x01,
            0x01,
            0x01,
            0x11,
            0x00,
            0xFF,
            0xC4,
            0x00,
            0x14,
            0x00,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x03,
            0xFF,
            0xC4,
            0x00,
            0x14,
            0x10,
            0x01,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0x00,
            0xFF,
            0xDA,
            0x00,
            0x08,
            0x01,
            0x01,
            0x00,
            0x00,
            0x3F,
            0x00,
            0x7F,
            0xFF,
            0xD9,
        ]
    )
    path.write_bytes(jpeg)


def _write_placeholder_video(path: Path) -> None:
    path.write_bytes(
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
        b"\x00\x00\x00\x08free"
    )


def _upload_file(file_path: Path, name: str, tags: str) -> dict:
    boundary = "----Phase0Boundary7MA4YWxkTrZu0gW"
    filename = file_path.name
    content_type = "image/jpeg" if file_path.suffix.lower() in {".jpg", ".jpeg"} else "video/mp4"
    file_bytes = file_path.read_bytes()

    parts: list[bytes] = []
    for field_name, field_value in (("name", name), ("tags", tags)):
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'.encode()
        )
        parts.append(field_value.encode("utf-8"))
        parts.append(b"\r\n")

    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    status, payload = _request(
        "POST",
        "/api/v1/media/upload",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if status != 200 or payload.get("code") != 0:
        raise RuntimeError(f"upload failed: {payload}")
    return payload["data"]


def main() -> int:
    status, health = _get("/health")
    print(f"[health] status={status} body={json.dumps(health, ensure_ascii=False)}")
    if health.get("code") != 0:
        print("数据中台不可用，请先启动 backend", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="phase0_rag_") as tmp:
        tmp_dir = Path(tmp)
        img = tmp_dir / "rag_career.jpg"
        vid = tmp_dir / "rag_benefit.mp4"
        _write_placeholder_image(img)
        _write_placeholder_video(vid)

        image = _upload_file(img, "RAG高薪就业示意", "RAG,就业,演示")
        video = _upload_file(vid, "RAG价值讲解视频", "RAG,就业,演示")
        print(f"[media] image_id={image['id']} url={image.get('url')}")
        print(f"[media] video_id={video['id']} url={video.get('url')}")

    knowledge_payload = {
        "title": "RAG就业与业务价值",
        "question": "学RAG有什么用",
        "answer": (
            "学 RAG 能帮你把企业私有知识接到大模型上，做可靠问答和客服助手。"
            "市场上相关岗位需求多、薪资也不错，是落地 AI 应用的一条实用路径。"
            "你可以边看图边听我说明它的好处。"
        ),
        "intent": "rag_career_value",
        "keywords": ["RAG", "学RAG", "有什么用", "就业", "高薪", "好处", "检索增强"],
        "status": "published",
        "priority": 20,
        "media_binds": [
            {"media_id": image["id"], "sort_order": 0, "caption": "RAG 就业方向示意"},
            {"media_id": video["id"], "sort_order": 1, "caption": "RAG 价值讲解"},
        ],
    }
    _, created = _post_json("/api/v1/knowledge", knowledge_payload)
    if created.get("code") != 0:
        raise RuntimeError(f"create knowledge failed: {created}")
    kid = created["data"]["id"]
    print(f"[knowledge] id={kid} title={created['data']['title']}")

    _, query = _post_json(
        "/api/v1/ai/query",
        {"query": "学RAG有什么用", "need_media": True, "limit": 3},
    )
    out_path = Path(__file__).resolve().parents[1] / "phase0_verify_rag_query.json"
    out_path.write_text(json.dumps(query, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[verify] wrote {out_path}")

    data = query.get("data") or {}
    if not data.get("matched") or not data.get("items"):
        print("验证失败：未命中知识", file=sys.stderr)
        return 2
    top = data["items"][0]
    media = top.get("media") or []
    if len(media) < 2:
        print(f"验证失败：媒体数量不足 media={media}", file=sys.stderr)
        return 3

    for m in media:
        url = (m.get("url") or "").replace("localhost", "127.0.0.1")
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"[media-ok] type={m['type']} http={resp.status} url={m['url']}")

    print("phase0 RAG seed ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
