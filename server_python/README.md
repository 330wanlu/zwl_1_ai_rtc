# Python AIGC Server

Node.js 版本的 Python 重写版，行为与 `Server/` 保持一致。

## 功能说明

本服务用于代理火山引擎（Volcengine）RTC 语音聊天 OpenAPI 请求，主要提供两个接口：

- `POST /proxy`：代理 `StartVoiceChat` / `StopVoiceChat` 请求
- `POST /getScenes`：获取所有可用场景配置（自动生成缺失的 RTC Token）

## 环境准备

```bash
pip install -r requirements.txt
```

## 启动命令

```bash
python app.py
```

服务默认监听 `http://localhost:3001`

## 使用须知

服务启动时会自动读取 `server_python/scenes` 下的所有文件作为可用场景。

因此您需要：

1. 复制 `server_python/scenes/Custom.json.example` 为 `Custom.json`，填入您的 AK/SK、AppId 等参数（`Custom.json` 已加入 `.gitignore`，不会提交到仓库）
2. 也可在 `server_python/scenes` 目录下参考模板创建新的场景配置文件
3. 确保 JSON 文件符合模板定义（大小写敏感）
4. 新增场景后需重启服务
5. 若 `RTCConfig.RoomId`、`RTCConfig.UserId`、`RTCConfig.Token` 任一为空，服务会自动生成

## 注意事项

- OpenAPI 签名逻辑与 Node 版 `@volcengine/openapi` Signer 对齐（`X-Content-Sha256`、`content-type` 不参与签名）
- RTC Token 生成逻辑与 Node 版 `token.js` 一致
- 相关错误会通过服务端接口返回

## 相关参数获取

- AccountConfig：https://console.volcengine.com/iam/keymanage/
- RTCConfig：https://console.volcengine.com/rtc/aigc/listRTC
- VoiceChat 参数：https://www.volcengine.com/docs/6348/1558163
