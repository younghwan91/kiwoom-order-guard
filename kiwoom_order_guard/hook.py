"""Claude Code 훅. 막을 때는 exit 2 + stderr (공식 플러그인 ``prod_guard`` 와 같은 방식).

``python -m kiwoom_order_guard.hook``       PreToolUse — 판정만, 상태는 읽기만
``python -m kiwoom_order_guard.hook post``  PostToolUse — 실제로 실행된 매수를 기록

판정 순서
1. 가드 설정·상태를 건드리거나 가드를 끄려는 도구 호출 → 차단
2. 주문 전송·주문표 생성 → :func:`rules.evaluate`. 한 호출에 주문이 여럿이면 앞 주문을 센 뒤 다음을 본다
주문이 아닌 호출은 정규식 한 번으로 통과한다.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

from . import parse
from .config import config_dir, load_config, state_dir
from .rules import counts_toward_limits, evaluate, order_key
from .state import STOP_FILE, State, locked

__all__ = ["decide", "main", "record"]

_PREFIX = "kiwoom-order-guard 차단:"
_RAW_HINT = re.compile(rb"kiwoomcli|kiwoom_cli|kw_runtime|kiwoom_order_submit")

_PROTECTED = (
    "/.config/kiwoom-order-guard",
    "/.local/state/kiwoom-order-guard",
    "\\kiwoom-order-guard\\config.toml",
    "\\kiwoom-order-guard\\STOP",
    "\\kiwoom-order-guard\\state",
    "kiwoom-order-guard/config.toml",
    "kiwoom-order-guard/STOP",
    "kiwoom-order-guard/state.json",
)
_GLOB = re.compile(r"kiwoom-o[\w-]*\*")
_STOP_DELETE = re.compile(r"\bSTOP\b.*(?:-delete|\brm\b|\bunlink\b|\bmv\b)|(?:\brm\b|\bunlink\b|\bmv\b).*\bSTOP\b")
# 사람만 하는 명령: 비상정지 해제, 설정 초기화 (따옴표로 감싸도)
_HUMAN_ONLY = re.compile(r"\b(?:kog|kiwoom_order_guard\.cli)[\s\"'\\]+(?:resume|init)\b")
_PLUGIN_OFF = re.compile(r"\bplugins?\s+(?:disable|uninstall|remove)\b[^\n]*kiwoom-order-guard")
_SETTINGS = re.compile(r"settings(?:\.local)?\.json")
_OUR_KEY_TRUE = re.compile(r'"kiwoom-order-guard@[^"]*"\s*:\s*true')
_OUR_KEY = re.compile(r'"kiwoom-order-guard@[^"]*"')
_HOOK_OFF = ("disableAllHooks", "KIWOOM_ORDER_GUARD_HOME")
_SCRIPT_EXT = (".sh", ".bash", ".zsh", ".py", ".ps1", ".bat", ".cmd")
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _guard_dirs() -> tuple[str, ...]:
    return str(config_dir()), str(state_dir())


def _touches_guard(event: dict) -> str | None:
    """가드를 무르는 호출이면 사유. 편집 도구는 대상 경로만 보고, 설정 파일일 때만 본문을 본다."""
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    name = str(event.get("tool_name") or "")
    if name == "Read":
        return None  # 읽기는 허용 — 왜 막혔는지 설명할 수 있어야 한다
    human = "가드 설정·상태와 가드 켜고 끄기는 사람이 직접 한다 — 에이전트는 건드리지 않는다"
    if name in _EDIT_TOOLS:
        path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        body = _written_text(tool_input)
        if any(d in path for d in _guard_dirs()) or any(p in path for p in _PROTECTED):
            return human
        if _SETTINGS.search(path):
            if any(h in body for h in _HOOK_OFF) or (_OUR_KEY.search(body) and not _OUR_KEY_TRUE.search(body)):
                return human
            if name == "Write" and _disables_on_write(path, str(tool_input.get("content") or "")):
                return human
            return None
        if path.lower().endswith(_SCRIPT_EXT) and parse.orderish_text(body):
            return "주문이 든 스크립트 파일은 쓰지 않는다 — 주문은 명령으로 한 건씩 직접 실행(가드가 검사할 수 있게)"
        return None
    blob = json.dumps(tool_input, ensure_ascii=False).replace("\\\\", "\\")
    if any(d in blob for d in _guard_dirs()) or any(p in blob for p in _PROTECTED) or _GLOB.search(blob):
        return human
    # Bash 에서 KIWOOM_ORDER_GUARD_HOME 을 바꿔도 훅 프로세스 환경은 그대로다 — 설정 파일 편집일 때만 본다.
    if _HUMAN_ONLY.search(blob) or _PLUGIN_OFF.search(blob) or "disableAllHooks" in blob:
        return human
    if "kiwoom-order-guard" in blob and _STOP_DELETE.search(blob):
        return human
    if ".config" in blob and _STOP_DELETE.search(blob):
        return human
    if _SETTINGS.search(blob) and ("kiwoom-order-guard" in blob or "enabledPlugins" in blob):
        return human
    return None


def _written_text(tool_input: dict) -> str:
    """편집 도구가 새로 쓰는 글만(Edit 의 old_string 은 빼고)."""
    parts = [str(tool_input.get(k) or "") for k in ("content", "new_string", "new_source")]
    for edit in tool_input.get("edits") or []:
        if isinstance(edit, dict):
            parts.append(str(edit.get("new_string") or ""))
    return "\n".join(parts)


def _disables_on_write(path: str, content: str) -> bool:
    """설정 파일을 통째로 쓰면서 지금 켜져 있는 이 플러그인 항목을 빼면 끄는 것과 같다."""
    try:
        from pathlib import Path

        current = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(_OUR_KEY_TRUE.search(current)) and not _OUR_KEY_TRUE.search(content)


def _risk_reducing(order: parse.Order) -> bool:
    return order.kind == "cancel" or (order.kind == "new" and order.side == "sell")


def decide(event: dict, *, now: float | None = None) -> list[str]:
    """막을 사유. 비면 통과. 상태는 읽기만 한다."""
    now = time.time() if now is None else now
    if reason := _touches_guard(event):
        return [reason]
    if not parse.looks_like_order(event):
        return []
    try:
        orders = parse.orders_from_event(event)
    except parse.Unparseable as exc:
        return [f"주문으로 보이는데 해석하지 못해 막는다 — {exc}"]
    if not orders:
        return []
    try:
        config = load_config()
    except (OSError, ValueError) as exc:
        # 설정을 못 읽어도 취소·매도(위험을 줄이는 쪽)는 막지 않는다. 이때 차단 목록은 적용되지 않는다.
        if all(_risk_reducing(o) for o in orders):
            return []
        return [f"설정을 읽지 못해 매수·정정을 막는다 — {exc}"]
    if any(counts_toward_limits(o) for o in orders) and not _state_writable():
        return [f"상태 폴더({state_dir()})에 쓸 수 없어 횟수 제한을 지킬 수 없다 — 매수를 막는다"]

    state = State.load(state_dir(), now)
    stopped = (config_dir() / STOP_FILE).exists()
    reasons: list[str] = []
    for order in orders:
        reasons += evaluate(order, config, now=now, stopped=stopped, orders_today=state.total,
                            orders_by_code=state.by_code, recent=state.recent)
        if counts_toward_limits(order):
            state.record(order_key(order), order.code.upper(), now)  # 같은 호출 안의 다음 주문이 보게(저장 안 함)
    return reasons


def _state_writable() -> bool:
    d = state_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(d, os.W_OK)


def record(event: dict, *, now: float | None = None) -> None:
    """실행된 호출의 매수를 기록한다. 해석이 안 되면 기록할 것도 없다(Pre 에서 막혔을 것)."""
    now = time.time() if now is None else now
    if not parse.looks_like_order(event):
        return
    try:
        orders = [o for o in parse.orders_from_event(event) if counts_toward_limits(o)]
    except parse.Unparseable:
        return
    if not orders:
        return
    with locked(state_dir()):
        state = State.load(state_dir(), now)
        for order in orders:
            state.record(order_key(order), order.code.upper(), now)
        state.save()


def _read_event() -> tuple[dict, bytes]:
    try:
        raw = sys.stdin.buffer.read()
    except OSError:
        return {}, b""
    try:
        event = json.loads(raw.decode("utf-8", errors="replace")) if raw.strip() else {}
    except ValueError:
        return {}, raw
    return (event if isinstance(event, dict) else {}), raw


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # 한글 Windows 콘솔(cp949)
        except (AttributeError, ValueError):
            pass
    event, raw = _read_event()
    if argv[:1] == ["post"]:
        try:
            record(event)
        except Exception as exc:  # noqa: BLE001 — 기록 실패로 도구 결과를 망치지 않는다
            print(f"kiwoom-order-guard 경고: 매수 기록 실패 — {exc!r}", file=sys.stderr)
        return 0
    if not event:
        if _RAW_HINT.search(raw):
            print(f"{_PREFIX} 훅 입력을 읽지 못했는데 주문 단서가 있어 막는다", file=sys.stderr)
            return 2
        return 0
    try:
        reasons = decide(event)
    except Exception as exc:  # noqa: BLE001 — 가드가 죽으면 주문 경로만 막는다
        if parse.looks_like_order(event) or _RAW_HINT.search(raw):
            print(f"{_PREFIX} 가드 내부 오류로 막는다 — {exc!r}", file=sys.stderr)
            return 2
        return 0
    if not reasons:
        return 0
    print(_PREFIX, file=sys.stderr)
    for reason in reasons[:5]:
        print(f"- {reason}", file=sys.stderr)
    if len(reasons) > 5:
        print(f"- 외 {len(reasons) - 5}건", file=sys.stderr)
    print("한도는 사용자가 직접 바꾼다(터미널에서 `kog config`).", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
