"""
date_info 工具：输入日期，返回星期几与距今天数。
- 支持 "2026-11-15"、"2026/11/15"、"2026.11.15" 等显式年月日格式
- 支持 "11月15日"、"11/15"、"11-15" 等月日格式，未写年份时按当前日期推算最近的未来日期
  （若该月日在今年已过，则取下一年同一天；若今天就是该月日，返回今天）
- 出错返回文字说明，不抛异常
"""
from __future__ import annotations

import re
from datetime import datetime, date, timezone, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from langchain.tools import tool

from tools._circuit_breaker import circuit_break


_BJ_TZ = timezone(timedelta(hours=8))
_WEEK_CN = ["一", "二", "三", "四", "五", "六", "日"]


def _today_beijing() -> date:
    return datetime.now(_BJ_TZ).date()


def _parse_full_date(s: str):
    """解析 YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD -> date；失败返回 None。"""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_md_date(s: str, today: date):
    """解析 11月15日 / 11/15 / 11-15，按当前日期推算最近未来日期。"""
    m = re.match(r"^\s*(\d{1,2})\s*[月/\-\.](\d{1,2})\s*日?\s*$", s)
    if not m:
        return None
    month = int(m.group(1))
    day = int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    # 先试今年
    try:
        this_year = date(today.year, month, day)
    except ValueError:
        return None
    if this_year >= today:
        return this_year
    # 今年已过，取下一年
    try:
        return date(today.year + 1, month, day)
    except ValueError:
        return None


def _format_delta(delta_days: int) -> str:
    if delta_days == 0:
        return "今天"
    if delta_days == 1:
        return "明天"
    if delta_days == -1:
        return "昨天"
    if delta_days > 0:
        return f"{delta_days} 天后"
    return f"{-delta_days} 天前"


class DateInfoInput(BaseModel):
    date_str: str = Field(
        ...,
        description='要查询的日期字符串。支持两种格式：1) 带年份，如 "2026-11-15"、"2026/11/15"；2) 仅月日，如 "11月15日"、"11/15"，未写年份时按当前日期推算最近的未来日期（已过的日期自动取下一年）'
    )
    output_type: Literal["line", "detail"] = Field(
        default="line",
        description='输出形式："line" 返回一行简短结果；"detail" 返回多行详情。默认 "line"'
    )


@tool("date_info", args_schema=DateInfoInput)
@circuit_break("date_info", error_prefix="日期查询失败",
              retry_hint="日期查询本回合已连续出错，请在涉及日期的位置标注「日期待确认」继续回复")
def date_info(date_str: str, output_type: str = "line") -> str:
    """查询给定日期是星期几、距离今天多少天。未写年份时按当前日期推算最近的未来日期。输出中出现日期+星期时请先调用本工具核对，避免把星期写错。"""
    try:
        today = _today_beijing()
        s = date_str.strip()
        d = _parse_full_date(s)
        if d is None:
            d = _parse_md_date(s, today)
        if d is None:
            return (
                f"日期查询失败：无法识别「{date_str}」，请使用 "
                f"「YYYY-MM-DD」（如 2026-11-15）或「M月D日」（如 11月15日）格式"
            )
        weekday = _WEEK_CN[d.weekday()]
        delta = (d - today).days
        rel = _format_delta(delta)

        if output_type == "detail":
            return (
                f"日期：{d.isoformat()}\n"
                f"星期：星期{weekday}\n"
                f"今天（{today.isoformat()}）距离：{rel}（相差 {delta} 天）"
            )
        return f"{d.isoformat()} 是星期{weekday}，{rel}"
    except Exception as e:
        return f"日期查询失败：{type(e).__name__}: {e}"


if __name__ == "__main__":
    import sys
    from tools._circuit_breaker import reset_all
    for arg in sys.argv[1:] or ["2026-11-15", "11月15日"]:
        reset_all()
        print(date_info.invoke({"date_str": arg}))
