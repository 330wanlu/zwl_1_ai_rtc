"""
工具函数模块
"""
import datetime
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from fastapi import Request

UNSIGNABLE_HEADERS = {
    "authorization",
    "content-type",
    "content-length",
    "user-agent",
    "presigned-expires",
    "expect",
}


def read_scenes(scenes_dir: str = "scenes") -> dict:
    """读取 scenes 目录下的所有文件（与 Node readFiles 行为一致）"""
    scenes = {}
    base_path = Path(__file__).parent / scenes_dir
    if not base_path.exists():
        return scenes

    for file_path in base_path.iterdir():
        if not file_path.is_file() or not file_path.name.endswith(".json"):
            continue
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        scenes[file_path.name.replace(".json", "")] = data
    return scenes


def assert_param(expression: Any, msg: str):
    """参数校验，失败时抛出异常（与 Node assert 行为一致）"""
    if not expression or (isinstance(expression, str) and " " in expression):
        print(f"\033[31m校验失败: {msg}\033[0m")
        raise Exception(msg)


def uri_escape(value: str) -> str:
    """与 @volcengine/openapi queryParamsToString 中的 uriEscape 保持一致"""
    return quote(str(value), safe="-_.!~*'()")


def query_params_to_string(params: dict) -> str:
    """与 @volcengine/openapi queryParamsToString 保持一致"""
    parts = []
    for key in sorted(params.keys()):
        val = params[key]
        if val is None:
            continue
        escaped_key = uri_escape(key)
        if not escaped_key:
            continue
        if isinstance(val, list):
            sorted_vals = sorted(uri_escape(v) for v in val)
            parts.append(f"{escaped_key}=" + f"&{escaped_key}=".join(sorted_vals))
        else:
            parts.append(f"{escaped_key}={uri_escape(val)}")
    return "&".join(parts)


def json_stringify(body: Any) -> str:
    """与 Node JSON.stringify 保持一致（紧凑格式、保留 Unicode）"""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)


def sign_volcengine_request(
    method: str,
    host: str,
    pathname: str,
    params: dict,
    body: Any,
    access_key_id: str,
    secret_key: str,
    service: str = "rtc",
    region: str = "cn-north-1",
) -> tuple[dict, str]:
    """
    与 @volcengine/openapi Signer 行为一致的 V4 签名。
    content-type 不参与签名，body 哈希写入 X-Content-Sha256。
    """
    body_str = json_stringify(body) if body else ""
    body_sha256 = hashlib.sha256(body_str.encode("utf-8")).hexdigest()

    x_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    headers = {
        "Host": host,
        "Content-type": "application/json",
        "X-Date": x_date,
        "X-Content-Sha256": body_sha256,
    }

    signable = []
    for key, value in headers.items():
        lower_key = key.lower()
        if lower_key not in UNSIGNABLE_HEADERS:
            normalized = re.sub(r"\s+", " ", str(value)).strip()
            signable.append((lower_key, normalized))
    signable.sort(key=lambda item: item[0])

    canonical_headers = "\n".join(f"{key}:{value}" for key, value in signable)
    signed_headers = ";".join(key for key, _ in signable)

    canonical_request = "\n".join([
        method.upper(),
        pathname or "/",
        query_params_to_string(params) or "",
        f"{canonical_headers}\n",
        signed_headers,
        body_sha256,
    ])

    credential_scope = f"{x_date[:8]}/{region}/{service}/request"
    hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = f"HMAC-SHA256\n{x_date}\n{credential_scope}\n{hashed_canonical_request}"

    def _hmac(key: bytes | str, message: str) -> bytes:
        if isinstance(key, str):
            key = key.encode("utf-8")
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    k_date = _hmac(secret_key, x_date[:8])
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    k_signing = _hmac(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"HMAC-SHA256 Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers, body_str


class APIWrapper:
    """API 响应包装器"""

    @staticmethod
    async def wrap(
        request: Request,
        api_name: str,
        logic: Callable,
        contain_response_metadata: bool = True,
    ):
        if not request.url.path.startswith(f"/{api_name}"):
            return None

        response_metadata = {"Action": api_name}
        try:
            result = await logic() if callable(logic) else logic
            if contain_response_metadata:
                return {
                    "ResponseMetadata": response_metadata,
                    "Result": result,
                }
            return result
        except Exception as e:
            response_metadata["Error"] = {
                "Code": -1,
                "Message": str(e) if str(e).startswith("Error:") else f"Error: {e}",
            }
            return {"ResponseMetadata": response_metadata}
