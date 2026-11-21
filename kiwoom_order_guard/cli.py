"""``kog`` — 사람이 터미널에서 쓰는 명령.

    kog init              기본 설정 파일 만들기
    kog config            설정 위치와 내용
    kog status            오늘 주문 시도 횟수·비상정지 여부
    kog stop / resume     비상정지 켜기 / 풀기 (resume 은 에이전트가 못 한다)
    kog check '<명령>'     Bash 명령 하나를 판정만 해 본다(기록하지 않음)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict

from . import parse
from .config import DEFAULT_TOML, INIT_MARKER, config_dir, load_config, state_dir
from .rules import evaluate
from .state import STOP_FILE, State

__all__ = ["main"]


def _check(command: str) -> int:
    event = {"tool_name": "Bash", "tool_input": {"command": command}}
    now = time.time()
    try:
        orders = parse.orders_from_event(event)
    except parse.Unparseable as exc:
        print(f"차단(해석 불가): {exc}")
        return 2
    if not orders:
        print("주문 아님 — 통과")
        return 0
    config = load_config()
    state = State.load(state_dir(), now)
    stopped = (config_dir() / STOP_FILE).exists()
    worst = 0
    for order in orders:
        print(json.dumps(asdict(order), ensure_ascii=False))
        reasons = evaluate(order, config, now=now, stopped=stopped, orders_today=state.total,
                           orders_by_code=state.by_code, recent=state.recent)
        for r in reasons:
            print(f"  차단: {r}")
        if not reasons:
            print("  통과")
        worst = max(worst, 2 if reasons else 0)
    return worst


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kog", description="키움 주문 가드")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("config")
    sub.add_parser("status")
    sub.add_parser("stop")
    sub.add_parser("resume")
    c = sub.add_parser("check")
    c.add_argument("command")
    a = p.parse_args(argv)

    home = config_dir()
    cfg_path = home / "config.toml"
    if a.cmd == "init":
        if cfg_path.exists():
            print(f"이미 있음: {cfg_path}")
            return 1
        home.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(DEFAULT_TOML, encoding="utf-8")
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / INIT_MARKER).write_text(str(cfg_path), encoding="utf-8")
        print(f"만듦: {cfg_path}")
        return 0
    if a.cmd == "config":
        print(cfg_path)
        print(cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else "(없음 — 기본값 사용. `kog init`)")
        print(load_config())
        return 0
    if a.cmd == "status":
        s = State.load(state_dir(), time.time())
        print(f"날짜 {s.date} · 오늘 매수 시도 {s.total}건 · 종목별 {s.by_code or '{}'}")
        print("비상정지: " + ("켜짐" if (home / STOP_FILE).exists() else "꺼짐"))
        return 0
    if a.cmd == "stop":
        home.mkdir(parents=True, exist_ok=True)
        (home / STOP_FILE).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        print("비상정지 켜짐 — 매수·정정이 막힌다. 매도·취소는 된다.")
        return 0
    if a.cmd == "resume":
        (home / STOP_FILE).unlink(missing_ok=True)
        print("비상정지 풀림")
        return 0
    if a.cmd == "check":
        return _check(a.command)
    return 1


if __name__ == "__main__":
    sys.exit(main())
