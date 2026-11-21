"""Claude Code PreToolUse 훅. 막을 때는 exit 2 + stderr (공식 플러그인 ``prod_guard`` 와 같은 방식).

1. 설정 폴더를 건드리는 도구 호출 → 차단 (에이전트가 한도를 스스로 풀지 못하게)
2. 주문 전송·주문표 생성 → :func:`rules.evaluate`
주문이 아닌 호출은 첫 검사에서 바로 통과한다.
"""

from __future__ import annotations

import json
import re
import sys
import time

from . import parse
from .config import config_dir, load_config, state_dir
from .rules import evaluate, order_key
from .state import STOP_FILE, State

__all__ = ["decide", "main"]

_PREFIX = "kiwoom-order-guard 차단:"


def _read_event() -> dict:
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except (OSError, ValueError):
        return {}
    return event if isinstance(event, dict) else {}


_PROTECTED = (
    ".config/kiwoom-order-guard",
    "kiwoom-order-guard/config.toml",
    "kiwoom-order-guard/STOP",
    "kiwoom-order-guard/state.json",
    "kiwoom-order-guard\\",
    "KIWOOM_ORDER_GUARD_HOME",
)
# 사람만 하는 명령: 비상정지 해제, 설정 초기화
_HUMAN_ONLY = re.compile(r"\b(kog|kiwoom_order_guard\.cli)\s+(resume|init)\b")
_DISABLE = re.compile(r"(disable|uninstall|remove|enabledPlugins|settings\.json)", re.IGNORECASE)


def _touches_config(event: dict) -> bool:
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return False
    if str(event.get("tool_name") or "") == "Read":
        return False  # 읽기는 허용 — 왜 막혔는지 설명할 수 있어야 한다
    blob = json.dumps(tool_input, ensure_ascii=False).replace("\\\\", "\\")
    if str(config_dir()) in blob or any(p in blob for p in _PROTECTED):
        return True
    if _HUMAN_ONLY.search(blob):
        return True
    # 가드 자체를 끄려는 시도(플러그인 비활성화·설정 편집)
    return "kiwoom-order-guard" in blob and bool(_DISABLE.search(blob))


def decide(event: dict, *, now: float | None = None) -> list[str]:
    """막을 사유. 비면 통과. 통과한 주문은 상태에 기록한다."""
    now = time.time() if now is None else now
    if _touches_config(event):
        return [f"가드 설정·상태 폴더({config_dir()})는 사람이 직접 바꾼다 — 에이전트는 건드리지 않는다"]
    if not parse.looks_like_order(event):
        return []
    try:
        orders = parse.orders_from_event(event)
    except parse.Unparseable as exc:
        return [f"주문으로 보이는데 해석하지 못해 막는다 — {exc}. 종목·수량·단가를 명시해 다시"]
    if not orders:
        return []
    try:
        config = load_config()
    except (OSError, ValueError) as exc:
        return [f"설정 파일을 읽지 못해 막는다 — {exc}"]

    state = State.load(state_dir(), now)
    stopped = (config_dir() / STOP_FILE).exists()
    reasons: list[str] = []
    for order in orders:
        reasons += evaluate(
            order,
            config,
            now=now,
            stopped=stopped,
            orders_today=state.total,
            orders_by_code=state.by_code,
            recent=state.recent,
        )
    if reasons:
        return reasons
    for order in orders:
        state.record(order_key(order), order.code.upper(), now, counts=order.kind == "new")
    state.save()
    return []


def main() -> int:
    event = _read_event()
    try:
        reasons = decide(event)
    except Exception as exc:  # noqa: BLE001 — 가드가 죽으면 주문 경로만 막는다
        if parse.looks_like_order(event):
            print(f"{_PREFIX} 가드 내부 오류로 막는다 — {exc!r}", file=sys.stderr)
            return 2
        return 0
    if not reasons:
        return 0
    print(_PREFIX, file=sys.stderr)
    for reason in reasons:
        print(f"- {reason}", file=sys.stderr)
    print("한도를 바꾸려면 사용자가 직접 설정 파일을 고친다(`kog config`로 위치 확인).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
