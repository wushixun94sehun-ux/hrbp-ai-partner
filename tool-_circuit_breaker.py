"""通用工具错误熔断装饰器：同一工具单次调用链内连续错误达到阈值后，本轮不再调用该工具。"""
import logging
import threading
from functools import wraps
from typing import Callable, Any

logger = logging.getLogger(__name__)

# 工具连续错误计数（进程内，每轮对话开始前由 supervisor 重置）
_error_counts: dict[str, int] = {}
_lock = threading.Lock()
DEFAULT_THRESHOLD = 2

_SKIPPED_MESSAGES = {
    "web_search": "联网搜索本回合已连续出错{threshold}次，后续不再调用；相关信息请注明「未能通过联网核实」并继续完成回复。",
    "estimate_budget": "预算估算本回合已连续出错{threshold}次，后续不再调用；请根据人均预算和人数给出粗估区间并注明「预算明细待核实」继续完成回复。",
    "calculate_weighted_scores": "加权总分计算本回合已连续出错{threshold}次，后续不再调用；请在表中直接使用用户提供的各维度分数，并注明「加权总分需人工核算」。",
    "rank_screening_results": "筛查排序本回合已连续出错{threshold}次，后续不再调用；请根据匹配规则人工给出建议结论并注明「排序未经工具核实」。",
    "amap_poi_search": "高德商户搜索本回合已连续出错{threshold}次，后续不再调用；相关商户信息请注明「未能通过地图核实」并继续完成回复。",
    "amap_weather": "高德天气查询本回合已连续出错{threshold}次，后续不再调用；请在风险预案中注明「天气待确认，临近活动前再查」并继续完成回复。",
    "amap_route_distance": "高德车程计算本回合已连续出错{threshold}次，后续不再调用；车程请写估计值并注明「车程待确认」并继续完成回复。",
}


def circuit_break(tool_name: str, threshold: int = DEFAULT_THRESHOLD,
                  retry_hint: str = "请检查参数再试一次；若再次失败请按提示兜底并继续回复。",
                  error_prefix: str = "工具调用失败"):
    """装饰器：为任意 @tool 函数添加连续错误熔断。

    - 连续 threshold 次错误后，后续调用直接返回跳过提示，不再真正执行。
    - 成功调用后计数器清零。
    - supervisor 会在每轮子 agent 调用前通过 reset_all() 重置所有计数器。
    """
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs) -> Any:
            # 熔断检查
            with _lock:
                if _error_counts.get(tool_name, 0) >= threshold:
                    skip_tpl = _SKIPPED_MESSAGES.get(
                        tool_name,
                        f"{tool_name} 本回合已连续出错{{threshold}}次，后续不再调用；请继续完成回复。"
                    )
                    return f"【{tool_name} 已跳过】" + skip_tpl.format(threshold=threshold)
            try:
                result = fn(*args, **kwargs)
                # 成功则清零
                with _lock:
                    _error_counts[tool_name] = 0
                return result
            except Exception as e:
                with _lock:
                    _error_counts[tool_name] = _error_counts.get(tool_name, 0) + 1
                    cnt = _error_counts[tool_name]
                logger.warning("tool %s error (%d/%d): %s", tool_name, cnt, threshold, e, exc_info=True)
                if cnt >= threshold:
                    skip_tpl = _SKIPPED_MESSAGES.get(
                        tool_name,
                        f"{tool_name} 已连续出错{{threshold}}次，后续不再调用；请按提示兜底并继续完成回复。"
                    )
                    return (f"{error_prefix}：{e}。" + skip_tpl.format(threshold=threshold))
                return f"{error_prefix}：{e}。{retry_hint}"
        # 保留原函数名/签名，使 @tool 装饰时名称正确
        wrapper.__name__ = getattr(fn, "__name__", tool_name)
        wrapper.__wrapped__ = fn
        return wrapper
    return decorator


def reset_all():
    """每轮对话开始前调用：清空所有工具的错误计数。"""
    with _lock:
        _error_counts.clear()
