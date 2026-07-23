"""
AIGC Server - Python 实现
代理火山引擎 RTC 语音聊天 OpenAPI 请求
"""
import asyncio
import json
import os
import re
import time
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from util import read_scenes, assert_param, APIWrapper, sign_volcengine_request
from token_manager import AccessToken, privileges
from knowledge_search import RAG_ENABLED, augment_messages_with_rag, retrieve_and_format
from data_platform_client import DATA_PLATFORM_ENABLED, query_ai
from media_session_store import DEFAULT_MEDIA_ROOM_ID, media_session_store

# 第三方 LLM 回调配置（火山 CustomLLM 回调使用）
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_ENDPOINT_ID = os.getenv("ARK_ENDPOINT_ID", "")
ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
# 可选：火山回调时携带的 Bearer Token，留空则不校验
CHAT_CALLBACK_TOKEN = os.getenv("CHAT_CALLBACK_TOKEN", "")
# 本地服务的公网地址（ngrok 等），用于注入 CustomLLM 的回调 URL
SERVER_URL = os.getenv("SERVER_URL", "").rstrip("/")
# 火山 OpenAPI / RAG 共用 AK/SK（Custom.json AccountConfig 留空时回退）
VOLC_ACCESS_KEY = os.getenv("VOLC_ACCESS_KEY", "")
VOLC_SECRET_KEY = os.getenv("VOLC_SECRET_KEY", "")
# RTC 应用（Custom.json RTCConfig 留空时回退；AppId 与 AppKey 成对配置）
RTC_APP_ID = os.getenv("RTC_APP_ID", "")
RTC_APP_KEY = os.getenv("RTC_APP_KEY", "")
# 中台命中后，SSE/TTS 口语文本最大字符数
SPOKEN_TEXT_MAX_CHARS = int(os.getenv("SPOKEN_TEXT_MAX_CHARS", "180"))
# 中台命中后，口语最多保留几句（默认 4，建议 2～4）
SPOKEN_TEXT_MAX_SENTENCES = int(os.getenv("SPOKEN_TEXT_MAX_SENTENCES", "4"))


def _resolve_account_credentials(account_config: dict) -> tuple[str, str]:
    """优先 scenes/Custom.json，留空则回退 .env 的 VOLC_ACCESS_KEY / VOLC_SECRET_KEY。"""
    ak = (account_config.get("accessKeyId") or VOLC_ACCESS_KEY).strip()
    sk = (account_config.get("secretKey") or VOLC_SECRET_KEY).strip()
    return ak, sk


def _resolve_rtc_app_id(app_id: str | None) -> str:
    """优先 Custom.json，留空则回退 .env 的 RTC_APP_ID。"""
    return (app_id or RTC_APP_ID).strip()


def _resolve_rtc_app_key(rtc_config: dict) -> str:
    """优先 RTCConfig.AppKey，留空则回退 .env 的 RTC_APP_KEY。"""
    return (rtc_config.get("AppKey") or RTC_APP_KEY).strip()


def _sync_scene_rtc_app(scenes: dict) -> None:
    """将解析后的 RTC AppId 同步到 RTCConfig 与 VoiceChat（两处需一致）。"""
    for scene_data in scenes.values():
        rtc_config = scene_data.setdefault("RTCConfig", {})
        voice_chat = scene_data.setdefault("VoiceChat", {})
        app_id = _resolve_rtc_app_id(rtc_config.get("AppId") or voice_chat.get("AppId"))
        if app_id:
            rtc_config["AppId"] = voice_chat["AppId"] = app_id


def _inject_callback_url(scenes: dict) -> None:
    """启动时把 SERVER_URL 注入到 CustomLLM 场景的 LLMConfig.Url（留空时才注入）。"""
    if not SERVER_URL:
        return
    for scene_data in scenes.values():
        llm = scene_data.get("VoiceChat", {}).get("Config", {}).get("LLMConfig", {})
        if llm.get("Mode") == "CustomLLM" and not llm.get("Url"):
            llm["Url"] = f"{SERVER_URL}/api/chat_callback"


SCENES = read_scenes()
_sync_scene_rtc_app(SCENES)
_inject_callback_url(SCENES)

app = FastAPI(title="AIGC Server", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ProxyRequest(BaseModel):
    SceneID: str


@app.post("/proxy")
async def proxy_api(
    request: Request,
    Action: str = Query(..., description="API 动作"),
    Version: str = Query("2024-12-01", description="API 版本"),
    body: ProxyRequest = Body(...),
):
    """代理 AIGC 的 OpenAPI 请求"""
    wrapper = APIWrapper()

    async def logic():
        assert_param(Action, "Action 不能为空")
        assert_param(Version, "Version 不能为空")
        assert_param(body.SceneID, "SceneID 不能为空, SceneID 用于指定场景的 JSON")

        scene_id = body.SceneID
        json_data = SCENES.get(scene_id)
        assert_param(json_data, f"{scene_id} 不存在, 请先在 server_python/scenes 下定义该场景的 JSON.")

        voice_chat = json_data.get("VoiceChat", {})
        account_config = json_data.get("AccountConfig", {})

        access_key_id, secret_key = _resolve_account_credentials(account_config)
        assert_param(
            access_key_id,
            "AccountConfig.accessKeyId 或 .env 的 VOLC_ACCESS_KEY 不能为空",
        )
        assert_param(
            secret_key,
            "AccountConfig.secretKey 或 .env 的 VOLC_SECRET_KEY 不能为空",
        )

        request_body = {}
        if Action == "StartVoiceChat":
            request_body = voice_chat
        elif Action == "StopVoiceChat":
            app_id = voice_chat.get("AppId")
            room_id = voice_chat.get("RoomId")
            task_id = voice_chat.get("TaskId")
            assert_param(app_id, "VoiceChat.AppId 不能为空")
            assert_param(room_id, "VoiceChat.RoomId 不能为空")
            assert_param(task_id, "VoiceChat.TaskId 不能为空")
            request_body = {"AppId": app_id, "RoomId": room_id, "TaskId": task_id}

        signed_headers, body_str = sign_volcengine_request(
            method="POST",
            host="rtc.volcengineapi.com",
            pathname="/",
            params={"Action": Action, "Version": Version},
            body=request_body,
            access_key_id=access_key_id,
            secret_key=secret_key,
            service="rtc",
            region="cn-north-1",
        )

        url = f"https://rtc.volcengineapi.com?Action={Action}&Version={Version}"

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                headers=signed_headers,
                content=body_str,
                timeout=30.0,
            )
            return resp.json()

    result = await wrapper.wrap(request, "proxy", logic, contain_response_metadata=False)
    if result is not None:
        return result

    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Not Found")


@app.post("/getScenes")
async def get_scenes(request: Request):
    """获取所有可用场景"""
    wrapper = APIWrapper()

    async def logic():
        scenes_list = []
        for scene_name, scene_data in SCENES.items():
            scene_config = scene_data.get("SceneConfig", {})
            rtc_config = scene_data.setdefault("RTCConfig", {})
            voice_chat = scene_data.setdefault("VoiceChat", {})

            app_id = rtc_config.get("AppId")
            assert_param(
                app_id,
                f"{scene_name} 场景的 RTCConfig.AppId 或 .env 的 RTC_APP_ID 不能为空",
            )

            token = rtc_config.get("Token")
            user_id = rtc_config.get("UserId")
            room_id = rtc_config.get("RoomId")

            if app_id and (not token or not user_id or not room_id):
                new_room_id = room_id or str(uuid.uuid4())
                new_user_id = user_id or str(uuid.uuid4())

                app_key = _resolve_rtc_app_key(rtc_config)
                assert_param(
                    app_key,
                    f"自动生成 Token 时, {scene_name} 场景的 RTCConfig.AppKey 或 .env 的 RTC_APP_KEY 不能为空",
                )

                expire_at = int(time.time()) + 24 * 3600
                key = AccessToken(app_id, app_key, new_room_id, new_user_id)
                key.add_privilege(privileges["PrivSubscribeStream"], expire_at)
                key.add_privilege(privileges["PrivPublishStream"], expire_at)
                key.add_privilege(privileges["privPublishScreenStream"], expire_at)
                key.expire_time(expire_at)

                rtc_config["RoomId"] = voice_chat["RoomId"] = new_room_id
                rtc_config["UserId"] = voice_chat["AgentConfig"]["TargetUserId"][0] = new_user_id
                rtc_config["Token"] = key.serialize()

            config = voice_chat.get("Config", {})
            llm_config = config.get("LLMConfig", {})
            vision_config = llm_config.get("VisionConfig", {})
            snapshot_config = vision_config.get("SnapshotConfig", {})
            avatar_config = config.get("AvatarConfig", {})

            scene_config["id"] = scene_name
            scene_config["botName"] = voice_chat.get("AgentConfig", {}).get("UserId")
            scene_config["isInterruptMode"] = config.get("InterruptMode") == 0
            scene_config["isVision"] = bool(vision_config.get("Enable"))
            scene_config["isScreenMode"] = snapshot_config.get("StreamType") == 1
            scene_config["isAvatarScene"] = bool(avatar_config.get("Enabled"))
            scene_config["avatarBgUrl"] = avatar_config.get("BackgroundUrl")

            rtc_config.pop("AppKey", None)

            scenes_list.append({
                "scene": scene_config,
                "rtc": rtc_config,
            })

        return {"scenes": scenes_list}

    result = await wrapper.wrap(request, "getScenes", logic)
    if result is not None:
        return result

    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Not Found")


def _extract_last_user_query(messages: list) -> str:
    """从 OpenAI messages 中取最近一条 user 文本。"""
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            # 兼容多模态 content 数组：只取 text 部分
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(str(part.get("text") or ""))
                    elif isinstance(part, str):
                        parts.append(part)
                text = "".join(parts).strip()
                if text:
                    return text
    return ""


def _limit_spoken_sentences(text: str, max_sentences: int) -> str:
    """按中文/英文句号切分，最多保留 max_sentences 句。"""
    s = (text or "").strip()
    if not s or max_sentences <= 0:
        return s
    parts = re.split(r"(?<=[。！？!?；;])", s)
    sentences = [p.strip() for p in parts if p and p.strip()]
    if not sentences:
        return s
    if len(sentences) <= max_sentences:
        return "".join(sentences).strip()
    return "".join(sentences[:max_sentences]).strip()


def _sanitize_spoken_text(
    text: str,
    max_chars: int = SPOKEN_TEXT_MAX_CHARS,
    max_sentences: int = SPOKEN_TEXT_MAX_SENTENCES,
) -> str:
    """清洗中台答案，供 TTS 朗读：去 URL/Markdown，限制句数与长度。绝不拼接媒体链接。"""
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"`[^`]*`", "", s)
    s = re.sub(r"[*_#>`\[\]\(\)]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _limit_spoken_sentences(s, max_sentences)
    if max_chars > 0 and len(s) > max_chars:
        cut = s[:max_chars]
        for sep in ("。", "！", "？", ";", "；", ".", "!", "?"):
            idx = cut.rfind(sep)
            if idx >= max_chars // 2:
                return cut[: idx + 1].strip()
        return cut.rstrip("，,、 ") + "。"
    return s


@app.get("/api/media/health")
async def media_health():
    """聚合探活：本服务 + 数据中台 AI（若已启用）+ 媒体缓存统计。"""
    from data_platform_client import (
        DATA_PLATFORM_BASE_URL,
        DATA_PLATFORM_ENABLED,
        health_check,
    )

    dp_data = None
    dp_ok = False
    if DATA_PLATFORM_ENABLED:
        dp_data = await health_check()
        dp_ok = dp_data is not None
    else:
        dp_data = {"status": "disabled"}

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "status": "ok",
            "data_platform_enabled": DATA_PLATFORM_ENABLED,
            "data_platform_base_url": DATA_PLATFORM_BASE_URL,
            "data_platform_ok": dp_ok if DATA_PLATFORM_ENABLED else None,
            "data_platform": dp_data,
            "default_media_room_id": DEFAULT_MEDIA_ROOM_ID,
            "media_store": media_session_store.stats(),
        },
    }


@app.get("/api/media/pending")
async def media_pending(
    roomId: str = Query(..., description="RTC 房间 ID，与 DEFAULT_MEDIA_ROOM_ID / 前端 roomId 对齐"),
):
    """弹出并返回该房间待展示的多模态媒体（读后清空）。"""
    items = media_session_store.pop_all(roomId)
    return {"code": 0, "message": "ok", "data": {"items": items}}


@app.post("/api/chat_callback")
async def chat_callback(request: Request):
    """
    火山 RTC 云端回调入口（CustomLLM）。
    优先可选查询数据中台（文字走 SSE/TTS，媒体写入 session store）；
    未命中则检索火山知识库并转发方舟；失败回退假回复。
    """
    # 可选鉴权：火山会以 Authorization: Bearer <APIKey> 回调
    if CHAT_CALLBACK_TOKEN:
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {CHAT_CALLBACK_TOKEN}":
            return JSONResponse(
                status_code=401,
                content={"Error": {"Code": "AuthenticationError", "Message": "invalid callback token"}},
            )

    try:
        body = await request.json()
    except Exception:
        body = {}

    messages = body.get("messages", [])
    model = body.get("model") or ARK_ENDPOINT_ID
    temperature = body.get("temperature", 0.7)
    max_tokens = body.get("max_tokens")
    top_p = body.get("top_p", 0.9)
    # MVP：媒体挂到默认房间；后续可由请求扩展字段覆盖
    media_room_id = (
        str(body.get("room_id") or body.get("roomId") or DEFAULT_MEDIA_ROOM_ID).strip()
        or DEFAULT_MEDIA_ROOM_ID
    )

    async def _prepare_messages_with_rag() -> list:
        """检索知识库并将参考资料注入 messages。"""
        if not RAG_ENABLED:
            print("\033[33m[RAG] 未配置知识库 AK/SK，跳过检索\033[0m")
            return messages

        user_query = _extract_last_user_query(messages)
        if not user_query:
            return messages

        try:
            context, raw = await retrieve_and_format(user_query, messages)
            count = len((raw.get("data") or {}).get("result_list") or [])
            print(f"\033[36m[RAG] 检索完成，命中 {count} 条参考资料\033[0m")
            return augment_messages_with_rag(messages, context)
        except Exception as e:
            print(f"\033[33m[RAG] 知识库检索失败，跳过 RAG: {e}\033[0m")
            return messages

    async def _try_data_platform_reply():
        """
        尝试中台多模态命中。
        返回 spoken_text（str）表示命中并应直接 SSE 播报；
        返回 None 表示未启用/未命中/失败，继续方舟链路。
        """
        if not DATA_PLATFORM_ENABLED:
            return None

        user_query = _extract_last_user_query(messages)
        if not user_query:
            return None

        result = await query_ai(user_query, need_media=True)
        if result is None:
            # 中台不可用：静默降级，避免面向用户的失败提示刷屏
            print("\033[33m[DataPlatform] 调用失败，静默降级方舟/RAG\033[0m")
            return None
        if not result.matched or not result.top:
            # 未命中属正常路径，不告警
            return None

        top = result.top
        spoken = _sanitize_spoken_text(top.answer)
        if not spoken:
            print("\033[33m[DataPlatform] 命中但答案为空，降级方舟/RAG\033[0m")
            return None

        media_payload = [m.to_dict() for m in top.media]
        # 仅当有可访问 URL 时写入侧通道；SSE 绝不带 url
        if media_payload:
            media_session_store.push(
                media_room_id,
                query=user_query,
                title=top.title,
                answer=spoken,
                media=media_payload,
                knowledge_id=top.knowledge_id,
                score=top.score,
            )
        else:
            print("\033[36m[DataPlatform] 命中无媒体，仅播文字\033[0m")

        print(
            f"\033[36m[DataPlatform] 命中播报 room={media_room_id} "
            f"title={top.title!r} media={len(media_payload)} "
            f"spoken_len={len(spoken)}\033[0m"
        )
        return spoken

    async def sse_stream():
        # 1) 数据中台优先：命中则只流式口语文本，媒体走 pending 侧通道
        spoken = await _try_data_platform_reply()
        if spoken is not None:
            async for chunk in _sse_plain_text_stream(model, spoken):
                yield chunk
            return

        # 2) 未命中：火山知识库 RAG + 方舟
        llm_messages = await _prepare_messages_with_rag()

        if ARK_API_KEY and ARK_ENDPOINT_ID:
            try:
                async for chunk in _call_ark_stream(llm_messages, temperature, max_tokens, top_p):
                    yield chunk
                return
            except Exception as e:
                print(f"\033[33m方舟调用失败，回退假回复: {e}\033[0m")
        # 兜底：假回复（用于第一步链路验证）
        async for chunk in _fake_reply(model, messages):
            yield chunk

    return StreamingResponse(sse_stream(), media_type="text/event-stream")


async def _call_ark_stream(messages, temperature, max_tokens, top_p):
    """转发到火山方舟 Ark（OpenAI 兼容），透传其 SSE 流。"""
    headers = {
        "Authorization": f"Bearer {ARK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": ARK_ENDPOINT_ID,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": temperature,
        "top_p": top_p,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("POST", ARK_API_URL, headers=headers, json=payload) as resp:
            if resp.status_code != 200:
                err = (await resp.aread()).decode("utf-8", errors="ignore")
                err_chunk = {
                    "Error": {"Code": str(resp.status_code), "Message": err}
                }
                yield f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return
            async for line in resp.aiter_lines():
                if not line:
                    continue
                # 方舟返回形如 "data: {...}" 或 "data: [DONE]"，直接透传并补 SSE 分隔
                yield line + "\n\n"


async def _sse_plain_text_stream(model, text: str, *, delay: float = 0.01):
    """把纯文本按 OpenAI chat.completion.chunk SSE 格式逐字输出，并以 [DONE] 结束。"""
    req_id = str(uuid.uuid4())
    created = int(time.time())
    content = text or ""

    def make(delta, finish=None, usage=None):
        chunk = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model or "data-platform",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            chunk["usage"] = usage
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    yield make({"role": "assistant"})
    for ch in content:
        yield make({"content": ch})
        if delay > 0:
            await asyncio.sleep(delay)
    yield make(
        {},
        finish="stop",
        usage={
            "prompt_tokens": 1,
            "completion_tokens": len(content),
            "total_tokens": 1 + len(content),
        },
    )
    yield "data: [DONE]\n\n"


async def _fake_reply(model, messages):
    """假回复：固定逐字输出，用于在没有方舟配置时验证 SSE 链路。"""
    last = ""
    if messages:
        last = messages[-1].get("content", "")
    text = f"（假回复，链路验证用）你刚才说：{last}。方舟未配置或不可用。"
    async for chunk in _sse_plain_text_stream(model or "fake", text, delay=0.02):
        yield chunk


if __name__ == "__main__":
    import uvicorn
    print("AIGC Server is running at http://localhost:3001")
    uvicorn.run(app, host="0.0.0.0", port=3001)
