"""
estimate_budget 工具：为组织文化助手提供活动预算明细与区间计算能力。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from langchain.tools import tool

from tools._circuit_breaker import circuit_break

logger = logging.getLogger(__name__)


class BudgetItem(dict):
    """费用项结构（用作 Pydantic 兼容的入参字段）。"""
    name: str                # 项目名称
    unit_price_min: float    # 单价下限
    unit_price_max: float    # 单价上限
    quantity: float          # 数量
    unit: str                # 计量单位（人/桌/间夜/辆/份 等）
    need_invoice: bool = False  # 是否需要开票


@tool
@circuit_break(
    tool_name="estimate_budget",
    retry_hint="请检查费用项字段是否齐全（每项需含 name/unit_price_min/unit_price_max/quantity/unit）后再试一次；若再次失败请根据人均预算和人数给出粗估区间并注明「预算明细待核实」继续回复。",
    error_prefix="预算估算失败",
)
def estimate_budget(
    items: List[dict],
    participant_count: int,
    budget_limit: Optional[float] = None,
    reserve_ratio: float = 0.10,
) -> str:
    """估算活动预算区间。

    输入：
      items: 费用项目列表，每项包含：
        - name: 项目名称（如"场地费""午餐""大巴"）
        - unit_price_min: 单价下限（元）
        - unit_price_max: 单价上限（元）；与下限相同表示固定单价
        - quantity: 数量
        - unit: 计量单位（如"人""桌""间夜""辆""份"）
        - need_invoice: 是否需要开票（布尔，默认 False）
      participant_count: 参与人数（必须 > 0）
      budget_limit: 用户给定的预算上限（元，可选）
      reserve_ratio: 备用金比例（默认 0.10，即 10%）

    输出：每项明细、合计区间、备用金、总预算区间、人均区间；
          若提供 budget_limit 且总预算区间上限超出，则提示超出金额。
    """
    return _estimate_budget_impl(items, participant_count, budget_limit, reserve_ratio)


def _estimate_budget_impl(
    items: List[dict],
    participant_count: int,
    budget_limit: Optional[float],
    reserve_ratio: float,
) -> str:
    if participant_count is None or participant_count <= 0:
        return "错误：参与人数 participant_count 必须为正整数。"
    if reserve_ratio < 0:
        return "错误：备用金比例 reserve_ratio 不能为负数。"

    lines: list[str] = []
    subtotal_min = 0.0
    subtotal_max = 0.0

    lines.append("【预算明细】")
    lines.append(f"{'项目':<16}{'单价区间(元)':<18}{'数量':<8}{'单位':<8}{'小计区间(元)':<22}{'开票'}")
    lines.append("-" * 90)

    for it in items:
        try:
            name = str(it["name"])
            up_min = float(it["unit_price_min"])
            up_max = float(it["unit_price_max"])
            qty = float(it["quantity"])
            unit = str(it.get("unit", ""))
            need_inv = bool(it.get("need_invoice", False))
        except (KeyError, TypeError, ValueError) as e:
            return f"错误：费用项字段缺失或格式不正确：{e}。每项需包含 name/unit_price_min/unit_price_max/quantity/unit。"

        if up_min < 0 or up_max < 0 or qty < 0:
            return f"错误：项目「{name}」单价与数量不能为负数。"
        if up_max < up_min:
            return f"错误：项目「{name}」单价上限不能小于单价下限。"

        s_min = round(up_min * qty, 2)
        s_max = round(up_max * qty, 2)
        subtotal_min += s_min
        subtotal_max += s_max

        price_str = f"{up_min:.2f}" if up_min == up_max else f"{up_min:.2f}~{up_max:.2f}"
        sub_str = f"{s_min:.2f}" if s_min == s_max else f"{s_min:.2f}~{s_max:.2f}"
        lines.append(
            f"{name:<14}{price_str:<18}{qty:<8}{unit:<8}{sub_str:<22}{'是' if need_inv else '否'}"
        )

    subtotal_min = round(subtotal_min, 2)
    subtotal_max = round(subtotal_max, 2)
    reserve_min = round(subtotal_min * reserve_ratio, 2)
    reserve_max = round(subtotal_max * reserve_ratio, 2)
    total_min = round(subtotal_min + reserve_min, 2)
    total_max = round(subtotal_max + reserve_max, 2)
    per_min = round(total_min / participant_count, 2)
    per_max = round(total_max / participant_count, 2)

    lines.append("-" * 90)
    lines.append(f"合计区间：{subtotal_min:.2f} ~ {subtotal_max:.2f} 元")
    lines.append(f"备用金（按 {reserve_ratio*100:.0f}%）：{reserve_min:.2f} ~ {reserve_max:.2f} 元")
    lines.append(f"总预算区间（含备用金）：{total_min:.2f} ~ {total_max:.2f} 元")
    lines.append(f"人均区间：{per_min:.2f} ~ {per_max:.2f} 元（共 {participant_count} 人）")

    if budget_limit is not None:
        try:
            bl = float(budget_limit)
            if total_max > bl:
                over = round(total_max - bl, 2)
                lines.append(f"⚠️ 总预算上限为 {bl:.2f} 元，按区间上限计算超出 {over:.2f} 元，建议削减部分项目或调整预算。")
            else:
                lines.append(f"✅ 总预算在 {bl:.2f} 元上限内。")
        except (TypeError, ValueError):
            lines.append(f"（预算上限 {budget_limit} 格式无效，已跳过超支判断）")

    return "\n".join(lines)
