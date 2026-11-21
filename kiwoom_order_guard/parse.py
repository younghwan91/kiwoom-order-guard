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

__all__ = ["Order", "Unparseable", "looks_like_order", "orderish_text", "orders_from_event"]

_CONTINUATION = re.compile(r"\\\r?\n")
# 따옴표·백슬래시를 지운 원문에서 찾는다 — 실행 파일 이름과 무관하게(`$KW`, `ki''woomcli` 도 걸리게).
_STRICT = re.compile(
    r"\borders\s+(?:buy|sell|modify|cancel|credit-\w+|gold-\w+)\b.*?--confirm(?![\w-])"
    r"|\bexchange\s+request\b.*?--confirm(?![\w-])"
    r"|\border-preview\b",
    re.DOTALL,
)
_SQUASH = re.compile(r"[\"'\\]")
_OPERATOR = re.compile(r"^[;&|()<>\n{}]+$")
# 한 명령으로 여러 번 실행되는 구문: 구간 첫 토큰 / 어디든 / 함수 정의
_LOOP_HEADS = {"for", "while", "until", "select", "function", "repeat"}
_LOOP_ANY = {"xargs", "parallel", "watch", "-exec", "-execdir", "-ok"}
_MCP_SUBMIT = re.compile(r"^mcp__.+__kiwoom_order_submit$")
# 인자를 실행하지 않고 글자로만 다루는 명령 — 이걸로 시작하는 구간의 "orders buy" 는 주문이 아니다
_TEXT_ONLY = {"grep", "egrep", "fgrep", "rg", "ag", "echo", "printf", "cat", "less", "more", "head", "tail",
              "wc", "git", "man", "ls", "diff"}

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
_GOLD_CODE = re.compile(r"^M0402\d{4}$")  # kwcli 금 현물 코드(M04020000·M04020100)
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


def orderish_text(text: str) -> bool:
    """따옴표를 지운 글이 주문 전송·주문표 생성처럼 보이나(스크립트 파일 검사에도 쓴다)."""
    return bool(_STRICT.search(_SQUASH.sub("", text)))


def looks_like_order(event: dict) -> bool:
    name = str(event.get("tool_name") or "")
    if _MCP_SUBMIT.match(name):
        return True
    return name == "Bash" and orderish_text(_command(event))


def orders_from_event(event: dict) -> list[Order]:
    """이 도구 호출이 전송(또는 주문표 생성)하려는 주문들. 주문이 없으면 빈 목록."""
    name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
    if _MCP_SUBMIT.match(name):
        return _from_mcp(tool_input)
    if name != "Bash":
        return []
    command = _command(event)
    if not orderish_text(command):
        return []
    return _from_segments(_segments(_tokens(command)))


def _command(event: dict) -> str:
    tool_input = event.get("tool_input")
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    # 공식 SKILL.md 예시처럼 `\` 줄바꿈으로 이어 쓴 명령은 한 줄로 붙인 뒤 나눈다.
    return _CONTINUATION.sub(" ", command)


def _tokens(command: str) -> list[str]:
    """따옴표를 먼저 풀고 연산자(; && || | ( ) { } 줄바꿈)를 따로 떼어 낸다."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError as exc:
        raise Unparseable(f"명령을 나눌 수 없음: {exc}") from exc


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for i, tok in enumerate(tokens):
        if tok == "()" or (tok == "(" and i + 1 < len(tokens) and tokens[i + 1] == ")" and i > 0
                           and not _OPERATOR.match(tokens[i - 1])):
            raise Unparseable("함수 안의 주문은 보지 않는다 — 주문은 한 건씩 단독 명령으로")
        if _OPERATOR.match(tok) or tok in ("{", "}"):
            segments.append([])
        else:
            segments[-1].append(tok)
    return [s for s in segments if s]


def _from_segments(segments: list[list[str]]) -> list[Order]:
    orders: list[Order] = []
    for seg in segments:
        repeats = seg[0] in _LOOP_HEADS or bool(_LOOP_ANY.intersection(seg))
        if repeats and any(orderish_text(" ".join(x)) for x in segments):
            raise Unparseable("반복문·xargs·find -exec 안의 주문은 보지 않는다 — 주문은 한 건씩 단독 명령으로")
        # kog check 와 글자만 다루는 명령(grep·echo·git…)은 인자를 실행하지 않는다 — 단 $( )·백틱은 셸이 실행한다
        text_only = seg[:2] == ["kog", "check"] or seg[0] in _TEXT_ONLY
        for tok in seg:
            runs = "$(" in tok or "`" in tok
            if (" " in tok or runs) and orderish_text(tok) and (runs or not text_only):
                raise Unparseable("따옴표·bash -c·$( ) 로 감싼 주문 명령은 해석하지 않는다 — 주문 명령을 그대로 실행")
        if text_only:
            continue
        order = _from_segment(seg)
        if order is not None:
            orders.append(order)
    return orders


def _from_segment(seg: list[str]) -> Order | None:
    """실행 파일이 무엇이든(kiwoomcli, $KW, python -m kiwoom_cli) 명령 구조로 찾는다."""
    if "order-preview" in seg:
        rest = seg[seg.index("order-preview") + 1:]
        if "--help" in rest or "-h" in rest:
            return None
        return _from_plugin(_flags(rest, allowed=_PLUGIN_FLAGS))
    for i in range(len(seg) - 2):
        market, group, action = seg[i], seg[i + 1], seg[i + 2]
        if group == "exchange" and action == "request" and market == "overseas":
            return Order("kiwoomcli", "fx", "", "", 0, None, "") if "--confirm" in seg[i + 3:] else None
        if group == "orders" and action in _WRITE_ORDERS:
            flags = _flags(seg[i + 3:])
            if "confirm" not in flags:
                return None  # 미전송 프리뷰
            return _from_cli(market, action, flags)
    if orderish_text(" ".join(seg)):
        raise Unparseable("주문처럼 보이는데 명령 구조를 읽지 못함")
    return None


def _from_invocation(tool: str, rest: list[str]) -> Order | None:
    """MCP 경로: command_path + options 를 kwcli 인자처럼 만든 뒤 같은 해석을 탄다."""
    return _from_segment([tool, *rest])


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
    raw_code = f.get("code", "").strip().upper()
    code = raw_code if action.startswith("gold-") and _GOLD_CODE.match(raw_code) else _check_code(raw_code, market)
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
