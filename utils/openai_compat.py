from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from loguru import logger
from openai import OpenAI


OPENAI_MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def create_openai_client(api_key: str, base_url: Optional[str] = None) -> OpenAI:
    client_kwargs: Dict[str, Any] = {
        "api_key": api_key,
        "max_retries": 0,
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    return OpenAI(**client_kwargs)


def is_official_openai_base_url(base_url: Optional[str]) -> bool:
    if not base_url:
        return True
    try:
        parsed = urlparse(base_url)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    return host in {"api.openai.com", "api.openai.com:443"}


def should_try_openai_prefix(model_name: str, base_url: Optional[str]) -> bool:
    if not model_name or "/" in model_name:
        return False
    if is_official_openai_base_url(base_url):
        return False
    return model_name.startswith(OPENAI_MODEL_PREFIXES)


def get_model_candidates(model_name: str, base_url: Optional[str]) -> List[str]:
    candidates = [model_name]
    if should_try_openai_prefix(model_name, base_url):
        candidates.append(f"openai/{model_name}")
    return candidates


def _should_continue_after_error(exc: Exception) -> bool:
    message = str(exc).lower()
    retry_markers = (
        "unknown provider",
        "provider for model",
        "model not found",
        "no such model",
        "unsupported model",
    )
    return any(marker in message for marker in retry_markers)


def _format_attempt_message(
    base_url: Optional[str],
    requested_model: str,
    attempts: List[Tuple[str, str]],
) -> str:
    lines = [
        f"LLM 请求失败，当前模型 `{requested_model}`，网关 `{base_url or 'default'}`。",
    ]
    for idx, (attempt_model, error_text) in enumerate(attempts, start=1):
        lines.append(f"{idx}. 尝试 `{attempt_model}` 失败: {error_text}")

    lines.append("这通常表示网关要求 provider 前缀，或当前 token 对目标模型没有权限。")
    lines.append("请检查 BASE_URL、MODEL_NAME，以及网关侧的 provider / model 授权配置。")
    return "\n".join(lines)


def create_chat_completion_with_fallback(
    client: OpenAI,
    model_name: str,
    messages: List[Dict[str, Any]],
    *,
    base_url: Optional[str] = None,
    logger_prefix: str = "LLM",
    **kwargs: Any,
) -> Tuple[Any, str]:
    attempts: List[Tuple[str, str]] = []
    last_exc: Optional[Exception] = None

    candidates = get_model_candidates(model_name, base_url)

    for idx, candidate in enumerate(candidates):
        try:
            response = client.chat.completions.create(
                model=candidate,
                messages=messages,
                **kwargs,
            )
            if candidate != model_name:
                logger.warning(
                    f"{logger_prefix}: model `{model_name}` failed on this gateway, "
                    f"fallback to `{candidate}` succeeded."
                )
            return response, candidate
        except Exception as exc:  # pragma: no cover - depends on remote gateway
            last_exc = exc
            attempts.append((candidate, str(exc)))

            has_more_candidates = idx < len(candidates) - 1
            if not has_more_candidates:
                break
            if not _should_continue_after_error(exc):
                break

    diagnostic_message = _format_attempt_message(base_url, model_name, attempts)
    raise RuntimeError(diagnostic_message) from last_exc


def probe_model_access(
    api_key: str,
    base_url: Optional[str],
    model_name: str,
    *,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    client = create_openai_client(api_key=api_key, base_url=base_url)
    messages = [{"role": "user", "content": "ping"}]

    try:
        _, resolved_model = create_chat_completion_with_fallback(
            client,
            model_name,
            messages,
            base_url=base_url,
            logger_prefix="LLM probe",
            timeout=timeout,
            max_tokens=8,
        )
        return {
            "success": True,
            "requested_model": model_name,
            "resolved_model": resolved_model,
            "base_url": base_url or "",
            "message": f"模型连通正常: {resolved_model}",
        }
    except Exception as exc:  # pragma: no cover - depends on remote gateway
        return {
            "success": False,
            "requested_model": model_name,
            "resolved_model": "",
            "base_url": base_url or "",
            "message": str(exc),
        }
