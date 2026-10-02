"""Safe arithmetic for KPI definitions stored in the database.

A KPI's formula is text such as "(revenue - cogs) / revenue * 100". It is parsed with Python's
`ast` module and only a tiny whitelist is accepted: numbers, measure names, + - * / and
unary minus, and two functions:

    prev(measure)   the measure in the previous period
    yoy(measure)    the measure in the same period a year earlier

Nothing else is allowed (no attributes, calls to anything else, comparisons, powers...), so a
stored formula can never run code. Arithmetic uses Decimal. A missing value or a division by
zero gives None ("can't be worked out"), never an exception or a made-up number.
"""

import ast
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MAX_LENGTH = 300
MAX_NODES = 120
FUNCTIONS = ("prev", "yoy")
_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_UNARY = (ast.USub, ast.UAdd)
_STRUCTURE = (ast.Expression, ast.Call, ast.Load, *_BINARY, *_UNARY)  # nodes that need no check


class ExpressionError(ValueError):
    """The formula is not valid, or uses a measure that doesn't exist."""


@dataclass(frozen=True)
class Compiled:
    source: str
    tree: ast.Expression
    # (shift, measure) pairs the formula reads, shift being "now", "prev" or "yoy".
    reads: frozenset[tuple[str, str]]

    @property
    def measures(self) -> frozenset[str]:
        return frozenset(name for _, name in self.reads)

    @property
    def shifts(self) -> frozenset[str]:
        return frozenset(shift for shift, _ in self.reads)


def compile_expression(
    source: str, known_measures: frozenset[str] | set[str] | None = None
) -> Compiled:
    if not isinstance(source, str) or not source.strip():
        raise ExpressionError("The formula is empty.")
    if len(source) > MAX_LENGTH:
        raise ExpressionError("The formula is too long.")
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"The formula can't be read: {exc.msg}.") from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise ExpressionError("The formula is too complicated.")
    reads: set[tuple[str, str]] = set()
    call_names: set[int] = set()  # the Name nodes that are function names, not measures
    for node in nodes:
        if isinstance(node, ast.Call):
            if not (
                isinstance(node.func, ast.Name)
                and node.func.id in FUNCTIONS
                and len(node.args) == 1
                and not node.keywords
                and isinstance(node.args[0], ast.Name)
            ):
                raise ExpressionError(
                    "Only prev(measure) and yoy(measure) can be used as functions."
                )
            call_names.add(id(node.func))
            reads.add((node.func.id, node.args[0].id))
    for node in nodes:
        if isinstance(node, ast.Name):
            if id(node) in call_names:
                continue
            if any(isinstance(p, ast.Call) and node in p.args for p in nodes):
                continue  # the argument of prev()/yoy(): recorded above
            reads.add(("now", node.id))
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int | float):
                raise ExpressionError("Only numbers are allowed in a formula.")
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, _BINARY):
                raise ExpressionError("Only + - * / are allowed.")
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _UNARY):
                raise ExpressionError("Only a minus sign is allowed in front of a value.")
        elif not isinstance(node, _STRUCTURE):
            raise ExpressionError(f"{type(node).__name__} isn't allowed in a formula.")
    if known_measures is not None:
        unknown = sorted({name for _, name in reads} - set(known_measures))
        if unknown:
            raise ExpressionError(f"Unknown measure: {', '.join(unknown)}.")
    return Compiled(source=source.strip(), tree=tree, reads=frozenset(reads))


def evaluate(compiled: Compiled, read: Callable[[str, str], Decimal | None]) -> Decimal | None:
    """The formula's value, or None if a value is missing or something divides by zero.

    `read(shift, measure)` supplies measure values for "now", "prev" or "yoy".
    """

    def walk(node: ast.AST) -> Decimal | None:
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            return read("now", node.id)
        if isinstance(node, ast.Call):
            return read(node.func.id, node.args[0].id)
        if isinstance(node, ast.UnaryOp):
            inner = walk(node.operand)
            if inner is None:
                return None
            return -inner if isinstance(node.op, ast.USub) else inner
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                return None
            return left / right
        raise ExpressionError("Unsupported formula")  # unreachable: compile_expression checked

    try:
        return walk(compiled.tree)
    except InvalidOperation:
        return None
