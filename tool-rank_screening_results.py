"""
rank_screening_results 工具
用于功能二（简历门槛筛查）的结论判定与排序。
输入：每位候选人的逐项评估列表（每项含要求类型、匹配程度），工具自己统计各匹配程度的项数，
     再按规则判定结论、排序，返回每位候选人的结论、名次和统计数字。
- "面试评估"项不计入统计
- 结论规则：必备条件中"低"和"未体现"合计 0 项 → 推荐进入面试；1 项 → 待定；≥2 项 → 暂不推荐
- 排序规则：推荐进入面试 > 待定 > 暂不推荐；结论相同 → 必备条件"高"的项数降序 → 加分项"高"降序 → 加分项"中"降序；仍相同则并列
- 支持单候选人（仅返回结论与统计）
"""
from __future__ import annotations

import logging
from typing import List, Dict, Any
from collections import Counter
from langchain.tools import tool

logger = logging.getLogger(__name__)


def _conclusion_from_counts(required_low_none: int) -> str:
    if required_low_none == 0:
        return "推荐进入面试"
    if required_low_none == 1:
        return "待定"
    return "暂不推荐"


_CONCLUSION_RANK = {"推荐进入面试": 0, "待定": 1, "暂不推荐": 2}


@tool
def rank_screening_results(
    candidates: List[Dict[str, Any]]
) -> str:
    """根据每位候选人的逐项评估列表，统计必备条件与加分项中各匹配程度的项数，
    判定建议结论并排序，返回每位候选人的名次、结论和统计数字。

    参数：
    - candidates: 候选人列表，每项字段：
        * candidate_id: str  候选人编号（如"候选人1"）
        * items: List[Dict]  逐项评估，每项字段：
            - requirement_type: "必备" | "加分"  （必须）
            - match_level: "高" | "中" | "低" | "未体现" | "面试评估"  （必须）

    返回：
    每位候选人的建议结论、名次、必备条件（高/中/低/未体现）计数、加分项（高/中）计数。
    """
    try:
        if not candidates:
            return "未提供候选人数据。"

        results = []
        for c in candidates:
            cid = c.get("candidate_id", "未知候选人")
            items = c.get("items", []) or []

            req_cnt = Counter({"高": 0, "中": 0, "低": 0, "未体现": 0})
            bonus_cnt = Counter({"高": 0, "中": 0})

            for it in items:
                rt = it.get("requirement_type", "")
                ml = it.get("match_level", "")
                if ml == "面试评估":
                    continue
                if rt == "必备":
                    if ml in req_cnt:
                        req_cnt[ml] += 1
                elif rt == "加分":
                    if ml in bonus_cnt:
                        bonus_cnt[ml] += 1

            required_low_none = req_cnt["低"] + req_cnt["未体现"]
            results.append({
                "candidate_id": cid,
                "conclusion": _conclusion_from_counts(required_low_none),
                "required_high": req_cnt["高"],
                "required_mid": req_cnt["中"],
                "required_low": req_cnt["低"],
                "required_none": req_cnt["未体现"],
                "bonus_high": bonus_cnt["高"],
                "bonus_mid": bonus_cnt["中"],
            })

        def sort_key(r):
            return (
                _CONCLUSION_RANK[r["conclusion"]],
                -r["required_high"],
                -r["bonus_high"],
                -r["bonus_mid"],
            )

        sorted_results = sorted(results, key=sort_key)

        n = len(sorted_results)
        ranks = [0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and sort_key(sorted_results[j + 1]) == sort_key(sorted_results[i]):
                j += 1
            for k in range(i, j + 1):
                ranks[k] = i + 1 if i == j else f"并列第{i + 1}"
            i = j + 1

        lines = []
        for rank, r in zip(ranks, sorted_results):
            lines.append(
                f"候选人：{r['candidate_id']}\n"
                f"  名次：{rank}\n"
                f"  建议结论：{r['conclusion']}\n"
                f"  必备条件：高{r['required_high']}项 / 中{r['required_mid']}项 / 低{r['required_low']}项 / 未体现{r['required_none']}项\n"
                f"  加分项：高{r['bonus_high']}项 / 中{r['bonus_mid']}项"
            )

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"rank_screening_results failed: {e}")
        return f"候选人排序统计失败：{e}。请检查候选人逐项评估列表的格式是否正确。"
