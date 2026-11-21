"""훅 이벤트에서 주문을 꺼낸다.

세 경로만 본다. 입력 모양은 전부 공식 자료에서 가져왔다(추측 금지 — 틀리면 가드가 조용히 통과시킨다).

1. 플러그인: ``python -m kw_runtime.cli order-preview --side buy --symbol 005930 --qty 10
   --order-type limit --price 70000`` (Kiwoom-Plugin ``skills/kiwoom-order/SKILL.md``, ``kw_runtime/cli.py``).
   전송(``order-confirm``)은 프리뷰가 만든 주문표 ID 만 받으므로 프리뷰에서 막으면 주문표가 생기지 않는다.
   이 argparse 는 ``allow_abbrev`` 가 기본값(True)이라 ``--mark`` 같은 줄임 플래그도 먹는다 → 아는 플래그 밖은 막는다.
2. CLI 직접: ``kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type limit --price 70000 --confirm``
   (kwcli 1.0.3 ``maps/arguments.csv``). ``--confirm`` 이 없으면 미전송 프리뷰라 보지 않는다.
3. MCP: ``kiwoom_order_submit(command_path, options, confirm)`` (kiwoom-exec ``server.py``).
   ``options`` 키는 ``order_type`` 이든 ``order-type`` 이든 CLI 에선 같은 플래그가 되고, ``None``·``False`` 값은
   빠진다(``options_to_args``).

탐지는 넓게, 해석은 좁게: 원문이 주문처럼 보이는데(공식 ``prod_guard`` 처럼 정규식) 구조 해석으로 모든 호출을
설명하지 못하면 :class:`Unparseable` — 호출부는 막는다. ``bash -c``·``$( )``·반복문 안의 주문이 여기 걸린다.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

__all__ = ["Order", "Unparseable", "looks_like_order", "orders_from_event"]

_CONTINUATION = re.compile(r"\\\r?\n")
_TOOL = re.compile(r"kiwoomcli|kiwoom_cli|kw_runtime")
_TOOL_TOKEN = re.compile(r"(?:^|[/\\])(kiwoomcli(?:\.exe)?|kiwoom_cli|kw_runtime[./\\]cli(?:\.py)?)$")
_TOOL_IN_TEXT = re.compile(r"kiwoomcli|kiwoom_cli|kw_runtime[./\\]cli")
_ORDERISH = re.compile(
    r"(?:kiwoomcli|kiwoom_cli).*?\b(?:orders\s+(?:buy|sell|modify|cancel|credit-\w+|gold-\w+)|exchange\s+request)\b"
    r".*?--confirm(?![\w-])"
    r"|kw_runtime.*?\border-preview\b",
    re.DOTALL,
)
_LOOPS = re.compile(r"(?<![\w-])(for|while|until|xargs|parallel|watch)(?![\w-])")
_OPERATOR = re.compile(r"^[;&|()<>\n]+$")
_MCP_SUBMIT = re.compile(r"^mcp__.+__kiwoom_order_submit$")

# kwcli 1.0.3 api_commands.csv 에서 safety_policy=order_write 인 명령 전부.
_WRITE_ORDERS = {
    "buy", "sell", "modify", "cancel",
    "credit-buy", "credit-sell", "credit-modify", "credit-cancel",
    "gold-buy", "gold-sell", "gold-modify", "gold-cancel",
}
# 플러그인 order-preview 의 argparse 플래그(kw_runtime/cli.py).
_PLUGIN_FLAGS = {"kind", "side", "symbol", "qty", "order-type", "price", "market", "exchange", "order-id", "stop-price"}
_OVERSEAS_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}
_DOMESTIC_EXCHANGES = {"KRX", "NXT", "SOR"}
_DOMESTIC_CODE = re.compile(r"^[0-9][0-9A-Z]{5}$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_FALSY = {False, None, "false", "False", "0", ""}  # False == 0


class Unparseable(ValueError):
    """주문 전송 시도로 보이지만 필드를 읽지 못했다."""


@dataclass(frozen=True)
class Order:
    source: str            # plugin-preview | kiwoomcli | mcp
    kind: str              # new | modify | cancel | fx(환전)
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
    return name == "Bash" and bool(_ORDERISH.search(_command(event)))


def orders_from_event(event: dict) -> list[Order]:
    """이 도구 호출이 전송(또는 주문표 생성)하려는 주문들. 주문이 없으면 빈 목록."""
    name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if _MCP_SUBMIT.match(name):
        return _from_mcp(tool_input)
    if name != "Bash":
        return []
    command = _command(event)
    if not _ORDERISH.search(command):
        return []
    if _LOOPS.search(command):
        raise Unparseable("반복문·xargs 안의 주문은 보지 않는다 — 주문은 한 건씩 단독 명령으로")
    orders, seen_tools = _from_tokens(_tokens(command))
    if seen_tools < len(_TOOL_IN_TEXT.findall(command)):
        raise Unparseable("따옴표·bash -c·$( ) 로 감싼 주문 명령은 해석하지 않는다 — 주문 명령을 그대로 실행")
    return orders  # 모든 호출을 설명했고 전송이 없으면(프리뷰·kog check) 빈 목록


def _command(event: dict) -> str:
    tool_input = event.get("tool_input")
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    # 공식 SKILL.md 예시처럼 `\` 줄바꿈으로 이어 쓴 명령은 한 줄로 붙인 뒤 나눈다.
    return _CONTINUATION.sub(" ", command)


def _tokens(command: str) -> list[str]:
    """따옴표를 먼저 풀고 연산자(; && || | ( ) 줄바꿈)를 따로 떼어 낸다."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError as exc:
        raise Unparseable(f"명령을 나눌 수 없음: {exc}") from exc


def _from_tokens(tokens: list[str]) -> tuple[list[Order], int]:
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if _OPERATOR.match(tok):
            segments.append([])
        else:
            segments[-1].append(tok)
    orders: list[Order] = []
    seen = 0
    for seg in segments:
        if seg[:2] == ["kog", "check"]:
            # 판정만 해 보는 명령 — 인자의 주문 문자열은 실행되지 않는다
            seen += sum(len(_TOOL_IN_TEXT.findall(t)) for t in seg[2:])
            continue
        for i, tok in enumerate(seg):
            m = _TOOL_TOKEN.search(tok)
            if not m:
                continue
            seen += 1
            order = _from_invocation(m.group(1), seg[i + 1:])
            if order is not None:
                orders.append(order)
    return orders, seen


def _from_invocation(tool: str, rest: list[str]) -> Order | None:
    if tool.startswith("kw_runtime"):
        if rest[:1] == ["order-preview"]:
            return _from_plugin(_flags(rest[1:], allowed=_PLUGIN_FLAGS))
        return None
    # kiwoomcli / python -m kiwoom_cli
    if len(rest) >= 3 and rest[0] == "overseas" and rest[1] == "exchange" and rest[2] == "request":
        return Order("kiwoomcli", "fx", "", "", 0, None, "") if "--confirm" in rest else None
    if len(rest) >= 3 and rest[1] == "orders" and rest[2] in _WRITE_ORDERS:
        flags = _flags(rest[3:])
        if "confirm" not in flags:
            return None  # 미전송 프리뷰
        return _from_cli(rest[0], rest[2], flags)
    return None


def _flags(tokens: list[str], *, allowed: set[str] | None = None) -> dict[str, str]:
    """``--qty 10`` / ``--qty=10`` / ``--confirm`` → {"qty": "10", "confirm": ""}."""
    out: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key, eq, val = tok[2:].partition("=")
            if allowed is not None and key not in allowed:
                raise Unparseable(f"모르는 플래그 --{key} (줄임 플래그 금지 — 전체 이름으로)")
            if key in out:
                raise Unparseable(f"--{key} 가 두 번 나옴")
            if eq:
                out[key] = val
            elif i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
                out[key] = tokens[i + 1]
                i += 1
            else:
                out[key] = ""
        i += 1
    return out


def _market(market: str | None, exchange: str, symbol: str) -> str:
    if market:
        return market
    if exchange.upper() in _OVERSEAS_EXCHANGES:
        return "overseas"
    if exchange.upper() in _DOMESTIC_EXCHANGES or _DOMESTIC_CODE.match(symbol):
        return "domestic"
    if _TICKER.match(symbol.upper()):
        return "overseas"
    raise Unparseable(f"국내·해외를 정할 수 없음(--market 없음, 종목 {symbol!r})")


def _check_code(code: str, market: str) -> str:
    code = code.strip().upper()
    ok = _DOMESTIC_CODE.match(code) if market == "domestic" else _TICKER.match(code)
    if not ok:
        raise Unparseable(f"종목은 코드로 적는다(국내 6자리, 해외 티커) — {code!r}. 이름이면 resolve 로 코드를 먼저 확인")
    return code


def _from_plugin(f: dict[str, str]) -> Order:
    kind = f.get("kind", "new") or "new"
    if kind == "cancel":
        return Order("plugin-preview", "cancel", "", "", 0, None, "")
    symbol = f.get("symbol", "")
    if kind == "new":
        side = f.get("side", "")
        if side not in ("buy", "sell"):
            raise Unparseable(f"매수·매도 구분을 읽지 못함: --side {side!r}")
        if not symbol:
            raise Unparseable("종목(--symbol)이 없음 — 활성 종목에 기대지 말고 명시")
        market = _market(f.get("market"), f.get("exchange", ""), symbol.upper())
        return Order("plugin-preview", "new", side, _check_code(symbol, market), _int(f, "qty"),
                     _price(f.get("price")), f.get("order-type", "") or "", market)
    if kind == "modify":
        # 정정은 런타임이 원주문에서 종목을 가져온다. 국내는 수량·단가 모두 필요(SKILL.md 7.2).
        market = f.get("market") or ("overseas" if f.get("exchange", "").upper() in _OVERSEAS_EXCHANGES else "domestic")
        if market == "overseas":
            return Order("plugin-preview", "modify", "", "", 0, _price(f.get("price")), "", "overseas")
        return Order("plugin-preview", "modify", "", "", _int(f, "qty"), _require_price(f), "", "domestic")
    raise Unparseable(f"알 수 없는 --kind {kind!r}")


def _from_cli(market: str, action: str, f: dict[str, str]) -> Order:
    if market not in ("domestic", "overseas"):
        raise Unparseable(f"알 수 없는 시장 {market!r}")
    if action.endswith("cancel"):
        return Order("kiwoomcli", "cancel", "", f.get("code", ""), 0, None, "", market)
    code = _check_code(f.get("code", ""), market)
    if action.endswith("modify"):
        if market == "overseas":
            return Order("kiwoomcli", "modify", "", code, 0, _price(f.get("price")), "", "overseas")
        return Order("kiwoomcli", "modify", "", code, _int(f, "qty"), _require_price(f), "", market)
    side = "sell" if action.endswith("sell") else "buy"
    return Order("kiwoomcli", "new", side, code, _int(f, "qty"), _price(f.get("price")),
                 f.get("order-type", "") or "", market)


def _from_mcp(tool_input: dict) -> list[Order]:
    if tool_input.get("confirm", False) in _FALSY:
        return []  # 서버는 confirm 이 참일 때만 --confirm 을 붙인다
    path = str(tool_input.get("command_path") or "").removeprefix("kiwoomcli ").split()
    raw = tool_input.get("options") or {}
    if not isinstance(raw, dict):
        raise Unparseable("options 가 dict 가 아님")
    # options_to_args 처럼 None·False 는 빼고, True 는 값 없는 플래그
    flags = {
        str(k).replace("_", "-").lstrip("-"): ("" if v is True else str(v))
        for k, v in raw.items()
        if v is not None and v is not False
    }
    flags["confirm"] = ""
    order = _from_invocation("kiwoomcli", [*path, *(_as_args(flags))])
    if order is None:
        raise Unparseable(f"주문 명령 경로를 읽지 못함: {tool_input.get('command_path')!r}")
    return [Order("mcp", order.kind, order.side, order.code, order.qty, order.price, order.order_type, order.market)]


def _as_args(flags: dict[str, str]) -> list[str]:
    out: list[str] = []
    for k, v in flags.items():
        out.append(f"--{k}")
        if v != "":
            out.append(v)
    return out


def _int(f: dict[str, str], key: str) -> int:
    raw = f.get(key, "")
    if raw == "":
        raise Unparseable(f"--{key} 가 없음")
    try:
        value = int(str(raw).replace(",", ""))
    except ValueError as exc:
        raise Unparseable(f"--{key} 를 정수로 읽지 못함: {raw!r}") from exc
    if value < 1:
        raise Unparseable(f"--{key} 가 1 미만: {value}")
    return value


def _require_price(f: dict[str, str]) -> float:
    price = _price(f.get("price"))
    if price is None:
        raise Unparseable("정정에는 --price 가 필요")
    return price


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
