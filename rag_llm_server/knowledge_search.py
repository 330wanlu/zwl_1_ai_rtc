"""
火山引擎 Viking 知识库检索模块。
在 LLM 回答前先用 AK/SK 签名调用 search_knowledge，将结果注入大模型上下文。
"""
import os
from typing import Any

import httpx
from dotenv import load_dotenv

from util import sign_volcengine_request

load_dotenv()

KNOWLEDGE_BASE_DOMAIN = os.getenv(
    "KNOWLEDGE_BASE_DOMAIN", "api-knowledgebase.mlp.cn-beijing.volces.com"
)
# 优先用知识库专用 AK/SK，否则回退到通用 VOLC_ACCESS_KEY / VOLC_SECRET_KEY
KNOWLEDGE_AK = os.getenv("KNOWLEDGE_AK") or os.getenv("VOLC_ACCESS_KEY", "")
KNOWLEDGE_SK = os.getenv("KNOWLEDGE_SK") or os.getenv("VOLC_SECRET_KEY", "")
# 兼容旧配置：若仍配置了 Bearer API Key，仅作提示，检索走 AK/SK
KNOWLEDGE_API_KEY = os.getenv("KNOWLEDGE_API_KEY", "")
COLLECTION_NAME = os.getenv("KNOWLEDGE_COLLECTION_NAME", "Rag_Knowledge")
PROJECT_NAME = os.getenv("KNOWLEDGE_PROJECT_NAME", "default")
LIMIT = int(os.getenv("KNOWLEDGE_LIMIT", "3"))
DENSE_WEIGHT = float(os.getenv("KNOWLEDGE_DENSE_WEIGHT", "0.5"))
KNOWLEDGE_SERVICE = os.getenv("KNOWLEDGE_SERVICE", "air")
KNOWLEDGE_REGION = os.getenv("KNOWLEDGE_REGION", "cn-north-1")

SEARCH_PATH = "/api/knowledge/collection/search_knowledge"

# 供 main.py 判断是否启用 RAG：有 AK/SK 即可检索
RAG_ENABLED = bool(KNOWLEDGE_AK and KNOWLEDGE_SK)


def _host() -> str:
    domain = KNOWLEDGE_BASE_DOMAIN
    if domain.startswith("http://"):
        return domain[len("http://") :].rstrip("/")
    if domain.startswith("https://"):
        return domain[len("https://") :].rstrip("/")
    return domain.rstrip("/")


def _base_url() -> str:
    return f"https://{_host()}"


def _build_search_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """提取 user/assistant 对话历史，供知识库 query rewrite 使用。"""
    history: list[dict[str, str]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            history.append({"role": role, "content": content.strip()})
    return history


def format_knowledge_context(result_list: list[dict[str, Any]]) -> str:
    """将检索结果格式化为 LLM 可读的参考资料文本。"""
    if not result_list:
        return "（未检索到相关参考资料）"

    parts: list[str] = []
    for idx, item in enumerate(result_list, start=1):
        doc_info = item.get("doc_info") or {}
        title = (
            item.get("chunk_title")
            or doc_info.get("title")
            or doc_info.get("doc_name")
            or f"片段{idx}"
        )
        content = (item.get("content") or "").strip()
        score = item.get("score")
        header = f"【资料{idx}】{title}"
        if score is not None:
            header += f"（相关度: {score:.3f}）"
        parts.append(f"{header}\n{content}" if content else header)
    return "\n\n".join(parts)


def augment_messages_with_rag(
    messages: list[dict[str, Any]], context: str
) -> list[dict[str, Any]]:
    """在最后一条 user 消息前注入知识库参考资料。"""
    augmented = list(messages)
    rag_msg = {
        "role": "system",
        "content": (
            "以下是从知识库检索到的参考资料，请严格基于这些内容回答用户问题。"
            "若资料不足以回答，请明确告知用户知识库中暂无相关信息。\n\n"
            f"{context}"
        ),
    }

    insert_idx = len(augmented)
    for i in range(len(augmented) - 1, -1, -1):
        if augmented[i].get("role") == "user":
            insert_idx = i
            break
    augmented.insert(insert_idx, rag_msg)
    return augmented


async def search_knowledge(
    query: str,
    history_messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    使用 AK/SK V4 签名调用火山知识库 search_knowledge。
    返回原始 API 响应 JSON；失败时抛出异常。
    """
    if not RAG_ENABLED:
        raise ValueError("知识库 AK/SK 未配置（KNOWLEDGE_AK/KNOWLEDGE_SK 或 VOLC_ACCESS_KEY/VOLC_SECRET_KEY）")

    query = query.strip()
    if not query:
        raise ValueError("检索 query 不能为空")

    history = _build_search_history(history_messages or [])
    # 多轮改写需要完整 messages；单轮关闭 rewrite，减少无效参数
    use_rewrite = len(history) >= 2
    pre_processing: dict[str, Any] = {
        "need_instruction": True,
        "rewrite": use_rewrite,
        "return_token_usage": True,
    }
    if use_rewrite:
        pre_processing["messages"] = history

    payload = {
        "project": PROJECT_NAME,
        "name": COLLECTION_NAME,
        "query": query,
        "limit": LIMIT,
        "dense_weight": DENSE_WEIGHT,
        "pre_processing": pre_processing,
        "post_processing": {
            "rerank_switch": False,
            "rerank_only_chunk": False,
            "get_attachment_link": False,
        },
    }

    host = _host()
    headers, body_str = sign_volcengine_request(
        method="POST",
        host=host,
        pathname=SEARCH_PATH,
        params={},
        body=payload,
        access_key_id=KNOWLEDGE_AK,
        secret_key=KNOWLEDGE_SK,
        service=KNOWLEDGE_SERVICE,
        region=KNOWLEDGE_REGION,
    )
    headers["Accept"] = "application/json"

    url = f"{_base_url()}{SEARCH_PATH}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, headers=headers, content=body_str.encode("utf-8"))
        if resp.status_code >= 400:
            raise RuntimeError(
                f"知识库 HTTP {resp.status_code}: {resp.text[:500]}"
            )
        body = resp.json()

    if body.get("code") != 0:
        raise RuntimeError(
            f"知识库检索失败: code={body.get('code')}, message={body.get('message')}"
        )
    return body


async def retrieve_and_format(
    query: str,
    history_messages: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    检索知识库并返回 (格式化上下文, 原始响应)。
    未命中时 context 为提示文本，不抛异常。
    """
    result = await search_knowledge(query, history_messages)
    data = result.get("data") or {}
    result_list = data.get("result_list") or []
    return format_knowledge_context(result_list), result
