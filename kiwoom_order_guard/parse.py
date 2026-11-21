"""훅 이벤트에서 주문을 꺼낸다.

세 경로만 본다. 입력 모양은 전부 공식 자료에서 가져왔다(추측 금지 — 틀리면 가드가 조용히 통과시킨다).

1. 플러그인: ``python -m kw_runtime.cli order-preview --side buy --symbol 005930 --qty 10
   --order-type limit --price 70000`` (Kiwoom-Plugin ``skills/kiwoom-order/SKILL.md``).
   전송(``order-confirm``)은 프리뷰가 만든 주문표 ID 만 받으므로 프리뷰에서 막으면 주문표가 생기지 않는다.
2. CLI 직접: ``kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type limit --price 70000 --confirm``
   (kwcli 1.0.3 ``maps/arguments.csv``). ``--confirm`` 이 없으면 미전송 프리뷰라 보지 않는다.
3. MCP: ``kiwoom_order_submit(command_path, options, confirm)`` (kiwoom-exec ``server.py``).
   ``options`` 키는 ``order_type`` 이든 ``order-type`` 이든 CLI 에선 같은 플래그가 된다(``options_to_args``).

주문처럼 보이는데 필요한 값을 못 읽으면 :class:`Unparseable` 을 올린다 — 호출부는 막는다.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

__all__ = ["Order", "Unparseable", "looks_like_order", "orders_from_event"]

# 이 글자들이 없으면 주문일 수 없다 — 무거운 해석 전에 거른다.
_HINTS = ("order-preview", "orders", "kiwoom_order_submit")
_SEGMENTS = re.compile(r"&&|\|\||[;|\n]")
_CONTINUATION = re.compile(r"\\\r?\n")
_MCP_SUBMIT = re.compile(r"^mcp__.+__kiwoom_order_submit$")
# kwcli 국내 주문 쓰기 명령(api_commands.csv 의 safety_policy=order_write 중 국내 주식).
_CLI_WRITE = {
    "buy", "sell", "modify", "cancel",
    "credit-buy", "credit-sell", "credit-modify", "credit-cancel",
    "gold-buy", "gold-sell", "gold-modify", "gold-cancel",
}


class Unparseable(ValueError):
    """주문 전송 시도로 보이지만 필드를 읽지 못했다."""


@dataclass(frozen=True)
class Order:
    source: str            # plugin-preview | kiwoomcli | mcp
    kind: str              # new | modify | cancel
    side: str              # buy | sell | "" (정정·취소는 모를 수 있다)
    code: str
    qty: int
    price: float | None    # 지정가 단가(국내 원, 해외 USD). 시장가류는 None
    order_type: str
    market: str = "domestic"

    @property
    def notional(self) -> float | None:
        return None if self.price is None else self.qty * self.price


def looks_like_order(event: dict) -> bool:
    name = str(event.get("tool_name") or "")
    if _MCP_SUBMIT.match(name):
        return True
    if name != "Bash":
        return False
    command = _command(event)
    return any(h in command for h in _HINTS)


def orders_from_event(event: dict) -> list[Order]:
    """이 도구 호출이 전송(또는 주문표 생성)하려는 주문들. 주문이 없으면 빈 목록."""
    name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if _MCP_SUBMIT.match(name):
        return _from_mcp(tool_input)
    if name == "Bash":
        out: list[Order] = []
        for segment in _SEGMENTS.split(_command(event)):
            order = _from_segment(segment)
            if order is not None:
                out.append(order)
        return out
    return []


def _command(event: dict) -> str:
    tool_input = event.get("tool_input")
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    # 공식 SKILL.md 예시처럼 `\` 줄바꿈으로 이어 쓴 명령은 한 줄로 붙인 뒤 나눈다.
    return _CONTINUATION.sub(" ", command)


# ------------------------------------------------------------------ Bash

def _from_segment(segment: str) -> Order | None:
    if not any(h in segment for h in _HINTS):
        return None
    try:
        tokens = shlex.split(segment)
    except ValueError as exc:
        raise Unparseable(f"명령을 나눌 수 없음: {exc}") from exc
    # 1) 플러그인 프리뷰
    for i in range(len(tokens) - 2):
        if tokens[i] == "-m" and tokens[i + 1] == "kw_runtime.cli" and tokens[i + 2] == "order-preview":
            return _from_plugin(_flags(tokens[i + 3:]))
    # 2) kiwoomcli ... domestic|overseas orders <write> ... --confirm
    for i, tok in enumerate(tokens):
        base = tok.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if base not in ("kiwoomcli", "kiwoomcli.exe"):
            continue
        rest = tokens[i + 1:]
        if len(rest) >= 3 and rest[1] == "orders" and rest[2] in _CLI_WRITE:
            flags = _flags(rest[3:])
            if "confirm" not in flags:
                return None  # 미전송 프리뷰
            return _from_cli(rest[0], rest[2], flags)
    return None


def _flags(tokens: list[str]) -> dict[str, str]:
    """``--qty 10`` / ``--qty=10`` / ``--confirm`` → {"qty": "10", "confirm": ""}."""
    out: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key, eq, val = tok[2:].partition("=")
            if eq:
                out[key] = val
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                out[key] = tokens[i + 1]
                i += 1
            else:
                out[key] = ""
        i += 1
    return out


def _from_plugin(f: dict[str, str]) -> Order:
    kind = f.get("kind", "new") or "new"
    if kind == "cancel":
        return Order("plugin-preview", "cancel", "", "", _int(f, "qty", required=False) or 0, None, "")
    market = f.get("market", "domestic") or "domestic"
    side = f.get("side", "")
    if kind == "new" and side not in ("buy", "sell"):
        raise Unparseable(f"매수·매도 구분을 읽지 못함: --side {side!r}")
    code = f.get("symbol", "")
    if kind == "new" and not code:
        raise Unparseable("종목(--symbol)이 없음")
    return Order(
        source="plugin-preview",
        kind=kind,
        side=side,
        code=code.upper() if market != "domestic" else code,
        qty=_int(f, "qty", required=kind == "new"),
        price=_price(f.get("price")),
        order_type=f.get("order-type", "") or "",
        market=market,
    )


def _from_cli(market: str, action: str, f: dict[str, str]) -> Order:
    if action.endswith("cancel"):
        return Order("kiwoomcli", "cancel", "", f.get("code", ""), _int(f, "qty", required=False) or 0, None, "")
    kind = "modify" if action.endswith("modify") else "new"
    side = "" if kind == "modify" else ("sell" if action.endswith("sell") else "buy")
    code = f.get("code", "")
    if not code:
        raise Unparseable("종목(--code)이 없음")
    # 정정도 --qty·--price 를 쓴다(arguments.csv: mdfy_qty·mdfy_uv). 해외 정정만 수량이 없다.
    qty_required = not (kind == "modify" and market == "overseas")
    return Order(
        source="kiwoomcli",
        kind=kind,
        side=side,
        code=code,
        qty=_int(f, "qty", required=qty_required),
        price=_price(f.get("price")),
        order_type=f.get("order-type", "") or "",
        market=market,
    )


# ------------------------------------------------------------------ MCP

def _from_mcp(tool_input: dict) -> list[Order]:
    if tool_input.get("confirm") is not True:
        return []  # confirm 이 정확히 true 가 아니면 서버가 보내지 않는다(server.py)
    path = str(tool_input.get("command_path") or "").removeprefix("kiwoomcli ").split()
    if len(path) < 3 or path[1] != "orders":
        raise Unparseable(f"주문 명령 경로를 읽지 못함: {tool_input.get('command_path')!r}")
    if path[2] not in _CLI_WRITE:
        raise Unparseable(f"알 수 없는 주문 명령: {path[2]!r}")
    raw = tool_input.get("options") or {}
    if not isinstance(raw, dict):
        raise Unparseable("options 가 dict 가 아님")
    flags = {str(k).replace("_", "-").lstrip("-"): ("" if v is True else str(v)) for k, v in raw.items()}
    order = _from_cli(path[0], path[2], flags)
    return [Order("mcp", order.kind, order.side, order.code, order.qty, order.price, order.order_type, order.market)]


# ------------------------------------------------------------------ 값

def _int(f: dict[str, str], key: str, *, required: bool = True) -> int:
    raw = f.get(key, "")
    if raw == "":
        if required:
            raise Unparseable(f"--{key} 가 없음")
        return 0
    try:
        value = int(str(raw).replace(",", ""))
    except ValueError as exc:
        raise Unparseable(f"--{key} 를 정수로 읽지 못함: {raw!r}") from exc
    if value < 0:
        raise Unparseable(f"--{key} 가 음수: {value}")
    return value


def _price(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(str(raw).replace(",", ""))
    except ValueError as exc:
        raise Unparseable(f"--price 를 숫자로 읽지 못함: {raw!r}") from exc
    if value <= 0:
        raise Unparseable(f"--price 가 0 이하: {raw}")
    return value
