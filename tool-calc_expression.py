"""
calc_expression: 安全计算四则运算表达式。
仅允许数字、小数点、正负号、加减乘除 (+ - * /) 以及括号，保留两位小数返回。
使用 Python 内置 AST 做白名单解析，避免 eval 任意代码执行风险。
"""
from __future__ import annotations
import ast
from langchain.tools import tool
from pydantic import BaseModel, Field
from tools._circuit_breaker import circuit_break


class CalcExpressionInput(BaseModel):
    expression: str = Field(
        ...,
        description="要计算的数学表达式，仅支持数字和 + - * / 与括号。"
                    "示例：\"(25+35)*120/1000\"、\"(12000-9800)/9800*100\"、\"89*30+1500\"",
    )
    scale: int = Field(
        2,
        description="结果保留的小数位数，默认 2 位。",
    )


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)


def _safe_eval(node: ast.AST) -> float:
    """递归计算 AST，仅允许数值、四则运算、括号、一元正负号。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"不支持的常量类型：{type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise ValueError(f"不支持的运算符：{type(node.op).__name__}")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("除数不能为 0")
            return left / right
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise ValueError(f"不支持的一元运算符：{type(node.op).__name__}")
        val = _safe_eval(node.operand)
        return val if isinstance(node.op, ast.UAdd) else -val
    raise ValueError(f"不支持的语法节点：{type(node).__name__}")


@tool("calc_expression", args_schema=CalcExpressionInput)
@circuit_break("calc_expression", error_prefix="四则运算计算失败",
              retry_hint="四则运算本回合已连续出错，相关计算请手动估算并在句末标注「（待核实）」")
def calc_expression(expression: str, scale: int = 2) -> str:
    """安全计算四则运算表达式（仅支持数字、+ - * / 和括号），返回结果字符串，保留 scale 位小数（默认 2 位）。"""
    try:
        if not isinstance(expression, str) or not expression.strip():
            return "四则运算计算失败：表达式为空"
        expr = expression.strip()
        # 白名单字符校验：数字、小数点、空格、括号、四则运算符
        allowed = set("0123456789.+-*/() ")
        for ch in expr:
            if ch not in allowed:
                return (f"四则运算计算失败：表达式包含非法字符「{ch}」，"
                        f"只允许数字、小数点、+、-、*、/、括号和空格")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as e:
            return f"四则运算计算失败：表达式语法错误（{e.msg}）"
        value = _safe_eval(tree)
        if not isinstance(value, (int, float)):
            return f"四则运算计算失败：结果不是数字"
        try:
            scale_int = int(scale)
            if scale_int < 0:
                scale_int = 2
        except Exception:
            scale_int = 2
        result = round(float(value), scale_int)
        # 保留小数位格式化（整数也补 0）
        return f"{expr} = {result:.{scale_int}f}"
    except ZeroDivisionError as e:
        return f"四则运算计算失败：除数不能为 0"
    except ValueError as e:
        return f"四则运算计算失败：{e}"
    except Exception as e:
        return f"四则运算计算失败：{type(e).__name__}: {e}"
