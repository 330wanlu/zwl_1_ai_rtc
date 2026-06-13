"""
AIGC Server - Python 实现
代理火山引擎 RTC 语音聊天 OpenAPI 请求
"""
import time
import uuid

import httpx
from fastapi import FastAPI, Request, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from util import read_scenes, assert_param, APIWrapper, sign_volcengine_request
from token_manager import AccessToken, privileges


SCENES = read_scenes()

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
            scene_config["isVision"] = vision_config.get("Enable")
            scene_config["isScreenMode"] = snapshot_config.get("StreamType") == 1
            scene_config["isAvatarScene"] = avatar_config.get("Enabled")
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


if __name__ == "__main__":
    import uvicorn
    print("AIGC Server is running at http://localhost:3001")
    uvicorn.run(app, host="0.0.0.0", port=3001)
