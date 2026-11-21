"""입력은 공식 자료의 예시를 그대로 쓴다(parse.py 머리말의 출처)."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from kiwoom_order_guard import hook, parse
from kiwoom_order_guard.config import Config

PLUGIN = 'uv run --no-project --python 3.13 --directory "${CLAUDE_PLUGIN_ROOT}" python -m kw_runtime.cli order-preview'
NOW = 1_763_700_000.0  # 2025-11-21 KST 근처 고정 시각


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KIWOOM_ORDER_GUARD_HOME", str(tmp_path))
    return tmp_path


def bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def mcp(command_path: str, options: dict, confirm=True, server="kiwoom-exec") -> dict:
    return {
        "tool_name": f"mcp__{server}__kiwoom_order_submit",
        "tool_input": {"command_path": command_path, "options": options, "confirm": confirm},
    }


def write_config(home, text: str) -> None:
    (home / "config.toml").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ 해석

def test_plugin_preview_from_skill_example():
    cmd = f"{PLUGIN} \\\n  --side buy --symbol 005930 --market domestic --exchange KRX --qty 10 --order-type limit --price 70000"
    [o] = parse.orders_from_event(bash(cmd))
    assert (o.source, o.kind, o.side, o.code, o.qty, o.price, o.order_type) == (
        "plugin-preview", "new", "buy", "005930", 10, 70000, "limit")
    assert o.notional == 700_000


def test_plugin_overseas_price_keeps_decimals():
    cmd = f"{PLUGIN} --side buy --symbol AAPL --market overseas --exchange NASDAQ --qty 10 --order-type limit --price 100.1234"
    [o] = parse.orders_from_event(bash(cmd))
    assert o.market == "overseas" and o.price == pytest.approx(100.1234)


def test_plugin_cancel_and_modify_examples():
    [c] = parse.orders_from_event(bash(f"{PLUGIN} --kind cancel --order-id 0001234 --qty 3"))
    assert c.kind == "cancel"
    [m] = parse.orders_from_event(bash(f"{PLUGIN} --kind modify --order-id 0001234 --qty 10 --price 71000"))
    assert (m.kind, m.qty, m.price) == ("modify", 10, 71000)
    [om] = parse.orders_from_event(
        bash(f"{PLUGIN} --kind modify --order-id US-9 --market overseas --exchange NASDAQ --price 101.5"))
    assert om.kind == "modify" and om.qty == 0


def test_kiwoomcli_preview_without_confirm_is_ignored():
    assert parse.orders_from_event(bash("kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type limit --price 70000")) == []


def test_kiwoomcli_confirm_in_chain_and_equals_form():
    cmd = "cd /tmp && /opt/bin/kiwoomcli domestic orders sell --code=005930 --qty=3 --order-type=market --confirm"
    [o] = parse.orders_from_event(bash(cmd))
    assert (o.side, o.qty, o.price, o.order_type) == ("sell", 3, None, "market")


def test_mcp_submit_needs_confirm_true():
    opts = {"code": "005930", "qty": 1, "order_type": "limit", "price": 70000}
    assert parse.orders_from_event(mcp("domestic orders buy", opts, confirm=False)) == []
    [o] = parse.orders_from_event(mcp("kiwoomcli domestic orders buy", opts))
    assert (o.source, o.side, o.price) == ("mcp", "buy", 70000)


@pytest.mark.parametrize("cmd", [
    f"{PLUGIN} --side buy --qty 10 --order-type limit --price 70000",          # 종목 생략(활성 종목 사용)
    f"{PLUGIN} --side buy --symbol 005930 --qty ten --order-type limit --price 70000",
    f"{PLUGIN} --symbol 005930 --qty 1 --order-type limit --price 70000",       # side 없음
    "kiwoomcli domestic orders buy --code 005930 --order-type market --confirm",  # qty 없음
    "kiwoomcli domestic orders buy --code '005930 --qty 1 --confirm",            # 따옴표 깨짐
])
def test_unreadable_order_is_blocked(cmd):
    reasons = hook.decide(bash(cmd), now=NOW)
    assert reasons and "해석하지 못해" in reasons[0]


def test_non_order_bash_passes_fast():
    assert hook.decide(bash("ls -la && git status"), now=NOW) == []
    assert hook.decide({"tool_name": "Edit", "tool_input": {"file_path": "/tmp/a.py"}}, now=NOW) == []


# ------------------------------------------------------------------ 규칙

def test_notional_cap_blocks_and_default_allows_small():
    ok = f"{PLUGIN} --side buy --symbol 005930 --qty 10 --order-type limit --price 70000"
    assert hook.decide(bash(ok), now=NOW) == []
    big = f"{PLUGIN} --side buy --symbol 000660 --qty 100 --order-type limit --price 70000"
    [r] = hook.decide(bash(big), now=NOW)
    assert "7,000,000원" in r and "1,000,000원" in r


def test_market_buy_blocked_market_sell_allowed():
    [r] = hook.decide(bash("kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type market --confirm"), now=NOW)
    assert "시장가" in r
    assert hook.decide(bash("kiwoomcli domestic orders sell --code 005930 --qty 1 --order-type market --confirm"),
                       now=NOW) == []


def test_sell_not_capped_by_default(home):
    cmd = "kiwoomcli domestic orders sell --code 005930 --qty 5000 --order-type limit --price 70000 --confirm"
    assert hook.decide(bash(cmd), now=NOW) == []
    write_config(home, "apply_caps_to_sell = true\n")
    reasons = hook.decide(bash(cmd.replace("5000", "5001")), now=NOW)
    assert any("수량" in r for r in reasons) and any("금액" in r for r in reasons)


def test_duplicate_within_window_blocked_then_allowed_later():
    opts = {"code": "005930", "qty": 1, "order_type": "limit", "price": 70000}
    assert hook.decide(mcp("domestic orders buy", opts), now=NOW) == []
    [r] = hook.decide(mcp("domestic orders buy", opts), now=NOW + 30)
    assert "반복" in r
    assert hook.decide(mcp("domestic orders buy", opts), now=NOW + 200) == []


def test_daily_and_per_code_limits(home):
    write_config(home, "max_orders_per_day = 3\nmax_orders_per_code = 2\nduplicate_window_sec = 0\n")
    cmd = "kiwoomcli domestic orders buy --code {c} --qty 1 --order-type limit --price 1000 --confirm"
    assert hook.decide(bash(cmd.format(c="005930")), now=NOW) == []
    assert hook.decide(bash(cmd.format(c="005930")), now=NOW + 1) == []
    [r] = hook.decide(bash(cmd.format(c="005930")), now=NOW + 2)
    assert "종목당" in r
    assert hook.decide(bash(cmd.format(c="000660")), now=NOW + 3) == []
    [r] = hook.decide(bash(cmd.format(c="035420")), now=NOW + 4)
    assert "하루 상한" in r
    # 다음 날(KST) 리셋
    assert hook.decide(bash(cmd.format(c="035420")), now=NOW + 86_400) == []


def test_blocked_orders_are_not_counted(home):
    write_config(home, "max_orders_per_day = 1\n")
    big = "kiwoomcli domestic orders buy --code 005930 --qty 1000 --order-type limit --price 70000 --confirm"
    assert hook.decide(bash(big), now=NOW)
    small = "kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type limit --price 70000 --confirm"
    assert hook.decide(bash(small), now=NOW + 1) == []


def test_stop_file_blocks_buy_allows_sell_and_cancel(home):
    (home / "STOP").write_text("x")
    [r] = hook.decide(bash("kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type limit --price 1000 --confirm"),
                      now=NOW)
    assert "비상정지" in r
    assert hook.decide(bash("kiwoomcli domestic orders sell --code 005930 --qty 1 --order-type limit --price 1000 --confirm"),
                       now=NOW) == []
    assert hook.decide(bash(f"{PLUGIN} --kind cancel --order-id 0001234"), now=NOW) == []


def test_block_and_white_lists(home):
    write_config(home, 'blocklist = ["005930"]\nwhitelist = ["000660", "005930"]\n')
    cmd = "kiwoomcli domestic orders buy --code {c} --qty 1 --order-type limit --price 1000 --confirm"
    assert "차단 목록" in hook.decide(bash(cmd.format(c="005930")), now=NOW)[0]
    assert "허용 목록" in hook.decide(bash(cmd.format(c="035420")), now=NOW)[0]
    assert hook.decide(bash(cmd.format(c="000660")), now=NOW) == []


def test_overseas_usd_cap():
    cmd = f"{PLUGIN} --side buy --symbol AAPL --market overseas --exchange NASDAQ --qty 10 --order-type limit --price 150"
    [r] = hook.decide(bash(cmd), now=NOW)
    assert "$1,500.00" in r


def test_bad_config_key_blocks_orders(home):
    write_config(home, "max_notional = 5\n")  # 오타
    [r] = hook.decide(bash("kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type limit --price 1000 --confirm"),
                      now=NOW)
    assert "설정 파일" in r


# ------------------------------------------------------------------ 가드 자체 보호

@pytest.mark.parametrize("event", [
    {"tool_name": "Write", "tool_input": {"file_path": "~/.config/kiwoom-order-guard/config.toml", "content": "x"}},
    {"tool_name": "Bash", "tool_input": {"command": "rm ~/.config/kiwoom-order-guard/STOP"}},
    {"tool_name": "Bash", "tool_input": {"command": "kog resume"}},
    {"tool_name": "Bash", "tool_input": {"command": "python -m kiwoom_order_guard.cli resume"}},
    {"tool_name": "Bash", "tool_input": {"command": "claude plugin disable kiwoom-order-guard"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/home/u/.claude/settings.json",
                                         "old_string": '"kiwoom-order-guard@kiwoom-order-guard": true',
                                         "new_string": '"kiwoom-order-guard@kiwoom-order-guard": false'}},
])
def test_agent_cannot_loosen_guard(event):
    [r] = hook.decide(event, now=NOW)
    assert "사람이 직접" in r


def test_expanded_home_path_is_protected(home):
    ev = {"tool_name": "Bash", "tool_input": {"command": f"sed -i s/1000000/99999999/ {home}/config.toml"}}
    assert hook.decide(ev, now=NOW)


def test_reading_config_with_read_tool_is_allowed(home):
    assert hook.decide({"tool_name": "Read", "tool_input": {"file_path": str(home / "config.toml")}}, now=NOW) == []
    assert hook.decide(bash("kog status && kog stop"), now=NOW) == []


# ------------------------------------------------------------------ 훅 프로세스

def _run_hook(event: dict, home) -> subprocess.CompletedProcess:
    env = {"KIWOOM_ORDER_GUARD_HOME": str(home), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, "-m", "kiwoom_order_guard.hook"], check=False, input=json.dumps(event),
                          capture_output=True, text=True, env=env, cwd=str(__import__("pathlib").Path(__file__).parents[1]))


def test_hook_exit_codes(home):
    blocked = _run_hook(bash("kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type market --confirm"), home)
    assert blocked.returncode == 2 and "kiwoom-order-guard 차단" in blocked.stderr
    ok = _run_hook(bash("echo hi"), home)
    assert ok.returncode == 0 and ok.stderr == ""
    garbage = subprocess.run([sys.executable, "-m", "kiwoom_order_guard.hook"], check=False, input="not json",
                             capture_output=True, text=True, env={"KIWOOM_ORDER_GUARD_HOME": str(home)},
                             cwd=str(__import__("pathlib").Path(__file__).parents[1]))
    assert garbage.returncode == 0


def test_non_order_decision_is_fast():
    ev = bash("git log --oneline -20 | head && ls")
    t = time.perf_counter()
    for _ in range(1000):
        hook.decide(ev, now=NOW)
    assert (time.perf_counter() - t) / 1000 < 0.001


def test_default_config_matches_toml():
    import tomllib

    from kiwoom_order_guard.config import DEFAULT_TOML
    data = tomllib.loads(DEFAULT_TOML)
    default = Config()
    for k, v in data.items():
        got = getattr(default, k)
        assert (sorted(got) if isinstance(got, frozenset) else got) == v, k
