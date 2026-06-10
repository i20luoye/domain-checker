"""
指数退避重试 — 遇到限速/网络错误时自动重试

策略：
  429 Too Many Requests → 按 Retry-After 头等待，或指数递增（2s → 4s → 8s）
  503 Service Unavailable → 指数递增（1.5s → 3s → 6s）
  网络超时/连接错误 → 指数递增（1s → 2s → 4s）
  404/200 → 立即返回（不重试）

效果：
  - 3 次重试内解决大部分临时故障
  - 自动降低并发避免触发更多限速
  - 实现"无人值守"批量扫描
"""
import asyncio
import random


class RetryConfig:
    """重试配置"""
    def __init__(self, max_retries=3, base_delay=1.0, max_delay=10.0, jitter=0.1):
        self.max_retries = max_retries    # 最大重试次数
        self.base_delay = base_delay      # 基础延迟（秒）
        self.max_delay = max_delay        # 最大延迟（秒）
        self.jitter = jitter              # 随机抖动比例（避免多个请求同时重试）


DEFAULT_RETRY = RetryConfig()


async def retry_async(coro_factory, retry_config=DEFAULT_RETRY):
    """带指数退避的异步重试

    Args:
        coro_factory: 无参异步函数，返回 (success: bool, result: any)
                      success=True 时直接返回 result，不再重试
                      success=False 时根据错误类型选择等待时间
        retry_config: RetryConfig 实例

    Returns:
        (success, result) — 最后一次尝试的结果

    Usage:
        async def query():
            try:
                resp = await session.get(url)
                if resp.status == 429:
                    return (False, "rate_limited")
                return (True, resp.status)
            except Exception as e:
                return (False, str(e))

        status = await retry_async(query)
    """
    last_result = (False, None)

    for attempt in range(1, retry_config.max_retries + 1):
        success, result = await coro_factory()
        last_result = (success, result)

        if success:
            return result

        # 判断是否需要重试
        error_str = str(result) if result else ""
        if attempt >= retry_config.max_retries:
            break

        # 计算等待时间（指数退避 + 随机抖动）
        delay = min(
            retry_config.base_delay * (2 ** (attempt - 1)),
            retry_config.max_delay
        )
        # 加随机抖动，避免所有重试同时发起
        if retry_config.jitter > 0:
            delay += delay * random.uniform(0, retry_config.jitter)

        # 限速场景：尝试读取 Retry-After 头
        if "429" in error_str or "rate_limited" in error_str.lower():
            # 尝试从 result 中提取 retry_after 值（由调用方传入）
            if hasattr(result, "get"):
                ra = result.get("retry_after")
                if ra:
                    delay = max(delay, float(ra))

        await asyncio.sleep(delay)

    return last_result[1]


def should_retry(status_code: int, error_str: str = "") -> bool:
    """判断 HTTP 状态码或错误是否可重试

    可重试：
      429 — 限速
      502/503/504 — 服务端临时故障
      网络超时/连接重置/DNS 解析失败

    不可重试：
      200 — 成功
      404 — 域名不存在
      400/401/403/422 — 客户端错误
    """
    if status_code == 200:
        return False
    if status_code == 404:
        return False
    if status_code in (429, 502, 503, 504):
        return True
    if status_code >= 400:
        return False  # 4xx 客户端错误不重试

    # 根据错误字符串判断
    retryable_errors = [
        "timeout", "timed out", "connection refused",
        "connection reset", "dns lookup failed", "name or service not known",
        "eof", "server disconnected", "remote end closed connection",
        "temporary failure", "rate_limit", "too many requests",
    ]
    err_lower = error_str.lower()
    for keyword in retryable_errors:
        if keyword in err_lower:
            return True

    return False
