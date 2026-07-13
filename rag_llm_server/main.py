"""
AIGC Server - Python 实现
代理火山引擎 RTC 语音聊天 OpenAPI 请求
"""
import asyncio
import json
import os
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

# 第三方 LLM 回调配置（火山 CustomLLM 回调使用）
ARK_API_KEY = os.getenv("ARK_API_KEY", "")
ARK_ENDPOINT_ID = os.getenv("ARK_ENDPOINT_ID", "")
ARK_API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
# 可选：火山回调时携带的 Bearer Token，留空则不校验
CHAT_CALLBACK_TOKEN = os.getenv("CHAT_CALLBACK_TOKEN", "")
# 本地服务的公网地址（ngrok 等），用于注入 CustomLLM 的回调 URL
SERVER_URL = os.getenv("SERVER_URL", "").rstrip("/")


def _inject_callback_url(scenes: dict) -> None:
    """启动时把 SERVER_URL 注入到 CustomLLM 场景的 LLMConfig.Url（留空时才注入）。"""
    if not SERVER_URL:
        return
    for scene_data in scenes.values():
        llm = scene_data.get("VoiceChat", {}).get("Config", {}).get("LLMConfig", {})
        if llm.get("Mode") == "CustomLLM" and not llm.get("Url"):
            llm["Url"] = f"{SERVER_URL}/api/chat_callback"


SCENES = read_scenes()
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

        assert_param(account_config.get("accessKeyId"), "AccountConfig.accessKeyId 不能为空")
        assert_param(account_config.get("secretKey"), "AccountConfig.secretKey 不能为空")

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
            access_key_id=account_config["accessKeyId"],
            secret_key=account_config["secretKey"],
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
            assert_param(app_id, f"{scene_name} 场景的 RTCConfig.AppId 不能为空")

            token = rtc_config.get("Token")
            user_id = rtc_config.get("UserId")
            room_id = rtc_config.get("RoomId")

            if app_id and (not token or not user_id or not room_id):
                new_room_id = room_id or str(uuid.uuid4())
                new_user_id = user_id or str(uuid.uuid4())

                app_key = rtc_config.get("AppKey")
                assert_param(app_key, f"自动生成 Token 时, {scene_name} 场景的 AppKey 不可为空")

                key = AccessToken(app_id, app_key, new_room_id, new_user_id)
                key.add_privilege(privileges["PrivSubscribeStream"], 0)
                key.add_privilege(privileges["PrivPublishStream"], 0)
                key.expire_time(int(time.time()) + 24 * 3600)

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


@app.post("/api/chat_callback")
async def chat_callback(request: Request):
    """
    火山 RTC 云端回调入口（CustomLLM）。
    接收火山按 OpenAI 格式发来的 SSE 请求，转发到火山方舟 Ark，
    并按火山接口标准（SSE + data: [DONE]）流式返回。
    若方舟调用失败，回退到假回复，保证链路可验证。
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

    async def _prepare_messages_with_rag() -> list:
        """检索知识库并将参考资料注入 messages。"""
        if not RAG_ENABLED:
            print("\033[33m[RAG] 未配置知识库 AK/SK，跳过检索\033[0m")
            return messages

        user_query = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_query = (msg.get("content") or "").strip()
                break
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

    async def sse_stream():
        llm_messages = await _prepare_messages_with_rag()

        # 优先走真实方舟
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


async def _fake_reply(model, messages):
    """假回复：固定逐字输出，用于在没有方舟配置时验证 SSE 链路。"""
    req_id = str(uuid.uuid4())
    created = int(time.time())
    last = ""
    if messages:
        last = messages[-1].get("content", "")
    text = f"（假回复，链路验证用）你刚才说：{last}。方舟未配置或不可用。"

    def make(delta, finish=None, usage=None):
        chunk = {
            "id": req_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model or "fake",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            chunk["usage"] = usage
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

    yield make({"role": "assistant"})
    for ch in text:
        yield make({"content": ch})
        await asyncio.sleep(0.02)
    yield make({}, finish="stop", usage={
        "prompt_tokens": 1,
        "completion_tokens": len(text),
        "total_tokens": 1 + len(text),
    })
    yield "data: [DONE]\n\n"


if __name__ == "__main__":
    import uvicorn
    print("AIGC Server is running at http://localhost:3001")
    uvicorn.run(app, host="0.0.0.0", port=3001)
