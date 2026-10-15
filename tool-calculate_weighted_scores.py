import logging
from typing import List, Dict
from langchain.tools import tool

logger = logging.getLogger(__name__)


@tool
def calculate_weighted_scores(
    dimensions: List[str],
    weights: List[float],
    candidates: List[Dict],
) -> str:
    """精确计算每位候选人在各能力维度上的加权总分，并输出每个维度“得分×权重=结果”的明细。

    权重单位为百分数（如 20 代表 20%），各维度权重合计应为 100。
    加权总分 = Σ(维度得分 × 该维度权重 ÷ 100)，四舍五入保留两位小数。

    Args:
        dimensions: 能力维度名称列表，例如 ["招聘执行", "数据能力", "沟通协调", "活动策划", "思维敏捷度"]。
        weights: 与 dimensions 一一对应的权重列表，单位为百分数（如 20 代表 20%），合计应为 100。
        candidates: 候选人列表，每项为 {"name": 候选人名称, "scores": {维度名: 得分}}，
                    其中 scores 的 key 必须与 dimensions 中的名称完全一致。
    Returns:
        每位候选人的加权总分（保留两位小数）及每个维度“得分×权重=结果”的明细文本。
    """
    try:
        if len(dimensions) != len(weights):
            return "错误：dimensions 与 weights 长度不一致，无法计算。"
        if not candidates:
            return "错误：未提供候选人数据。"

        total_weight = round(sum(weights), 2)
        lines = []
        for cand in candidates:
            name = cand.get("name", "候选人")
            scores = cand.get("scores", {}) or {}
            lines.append(f"候选人：{name}")
            weighted_total = 0.0
            for dim, weight in zip(dimensions, weights):
                score = scores.get(dim)
                if score is None:
                    lines.append(f"  {dim}：缺少得分（未提供）")
                    continue
                s = float(score)
                w = float(weight)
                weighted = round(s * w / 100.0, 2)
                weighted_total += s * w / 100.0
                lines.append(f"  {dim}：{s} × {w}% = {weighted}")
            lines.append(f"  加权总分：{round(weighted_total, 2)}")

        summary = (
            f"\n维度权重合计：{total_weight}%（应为 100%，若不等于 100 请检查输入）。"
            f"\n共计算 {len(candidates)} 位候选人。"
        )
        return "\n".join(lines) + summary
    except Exception as e:
        logger.warning(f"calculate_weighted_scores failed: {e}")
        return f"加权总分计算失败：{e}。请检查维度、权重和候选人得分的格式是否正确。"
