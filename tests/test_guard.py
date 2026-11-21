"""입력은 공식 자료의 예시를 그대로 쓴다(parse.py 머리말의 출처). 우회·오탐 사례는 리뷰에서 재현된 것."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kiwoom_order_guard import hook, parse
from kiwoom_order_guard.config import DEFAULT_TOML, Config

ROOT = Path(__file__).parents[1]
PLUGIN = 'uv run --no-project --python 3.13 --directory "${CLAUDE_PLUGIN_ROOT}" python -m kw_runtime.cli order-preview'
BUY = "kiwoomcli domestic orders buy --code {c} --qty {q} --order-type limit --price {p} --confirm"
NOW = 1_763_700_000.0


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


def send(event: dict, now: float) -> list[str]:
    """Pre 판정 → 통과하면 실행된 것으로 보고 Post 기록."""
    reasons = hook.decide(event, now=now)
    if not reasons:
        hook.record(event, now=now)
    return reasons


def buy(c="005930", q=1, p=1000) -> dict:
    return bash(BUY.format(c=c, q=q, p=p))


def write_config(home, text: str) -> None:
    (home / "config.toml").write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ 해석 (공식 예시)

def test_plugin_preview_from_skill_example_with_continuation():
    cmd = f"{PLUGIN} \\\n  --side buy --symbol 005930 --market domestic --exchange KRX --qty 10 --order-type limit --price 70000"
    [o] = parse.orders_from_event(bash(cmd))
    assert (o.source, o.kind, o.side, o.code, o.qty, o.price, o.order_type) == (
        "plugin-preview", "new", "buy", "005930", 10, 70000, "limit")


def test_plugin_overseas_example():
    cmd = f"{PLUGIN} --side buy --symbol AAPL --market overseas --exchange NASDAQ --qty 10 --order-type limit --price 100.1234"
    [o] = parse.orders_from_event(bash(cmd))
    assert o.market == "overseas" and o.price == pytest.approx(100.1234)


def test_plugin_cancel_and_modify_examples():
    [c] = parse.orders_from_event(bash(f"{PLUGIN} --kind cancel --order-id 0001234 --qty 3"))
    assert c.kind == "cancel"
    [m] = parse.orders_from_event(bash(f"{PLUGIN} --kind modify --order-id 0001234 --qty 10 --price 71000"))
    assert (m.kind, m.market, m.qty, m.price) == ("modify", "domestic", 10, 71000)
    [om] = parse.orders_from_event(
        bash(f"{PLUGIN} --kind modify --order-id US-9 --market overseas --exchange NASDAQ --price 101.5"))
    assert (om.kind, om.market) == ("modify", "overseas")


def test_kiwoomcli_preview_without_confirm_is_ignored():
    assert parse.orders_from_event(bash(BUY.format(c="005930", q=1, p=1000).replace(" --confirm", ""))) == []
    assert hook.decide(bash("kiwoomcli domestic stocks info --code 005930"), now=NOW) == []


def test_kiwoomcli_chain_equals_form_and_module_form():
    [o] = parse.orders_from_event(bash(
        "cd /tmp && /opt/bin/kiwoomcli domestic orders sell --code=005930 --qty=3 --order-type=market --confirm"))
    assert (o.side, o.qty, o.price) == ("sell", 3, None)
    [o] = parse.orders_from_event(bash("python -m kiwoom_cli domestic orders buy --code 005930 --qty 1 "
                                       "--order-type limit --price 1000 --confirm"))
    assert o.side == "buy"


def test_mcp_confirm_truthiness_and_null_options():
    opts = {"code": "005930", "qty": 1, "order_type": "limit", "price": 70000}
    for falsy in (False, None, 0, "false", ""):
        assert parse.orders_from_event(mcp("domestic orders buy", opts, confirm=falsy)) == []
    for truthy in (True, "true", 1):
        [o] = parse.orders_from_event(mcp("kiwoomcli domestic orders buy", opts, confirm=truthy))
        assert o.source == "mcp"
    # options_to_args 처럼 None 은 빠진다 → 시장가 매도로 해석
    [o] = parse.orders_from_event(mcp("domestic orders sell", {"code": "005930", "qty": 1,
                                                               "order_type": "market", "price": None}))
    assert o.price is None and hook.decide(mcp("domestic orders sell", {"code": "005930", "qty": 1,
                                                                        "order_type": "market", "price": None}),
                                           now=NOW) == []


# ------------------------------------------------------------------ 해석 실패 → 막는다

@pytest.mark.parametrize("cmd", [
    f"{PLUGIN} --side buy --qty 10 --order-type limit --price 70000",               # 종목 생략
    f"{PLUGIN} --side buy --symbol 삼성전자 --qty 1 --order-type limit --price 70000",  # 이름(차단 목록 우회)
    f"{PLUGIN} --side buy --symbol 005930 --qty ten --order-type limit --price 70000",
    f"{PLUGIN} --symbol 005930 --qty 1 --order-type limit --price 70000",            # side 없음
    f"{PLUGIN} --side buy --symbol AAPL --mark overseas --qty 1 --order-type limit --price 1",  # 줄임 플래그
    f"{PLUGIN} --kind modify --order-id 1 --q 50000 --price 70000",                  # 줄임 플래그
    f"{PLUGIN} --kind modify --order-id 1 --price 70000",                            # 국내 정정 수량 없음
    "kiwoomcli domestic orders buy --code 005930 --order-type market --confirm",     # qty 없음
    "kiwoomcli domestic orders buy --code $C --qty 1 --order-type limit --price 1 --confirm",
    "kiwoomcli domestic orders buy --code '005930 --qty 1 --confirm",                # 따옴표 깨짐
    f'bash -c "{BUY.format(c="005930", q=1, p=1000)}"',
    f'echo "$({BUY.format(c="005930", q=1, p=1000)})"',
    f"for i in $(seq 50); do {BUY.format(c='005930', q=1, p=1000)}; done",
    f"seq 50 | xargs -I{{}} {BUY.format(c='005930', q=1, p=1000)}",
    "python -m kiwoom_cli.main domestic orders buy --code 005930 --qty 1 --price 1 --confirm",
])
def test_unreadable_order_is_blocked(cmd):
    reasons = hook.decide(bash(cmd), now=NOW)
    assert reasons and "해석하지 못해" in reasons[0], reasons


def test_subshell_order_is_parsed_and_checked():
    big = BUY.format(c="005930", q=100, p=70000)
    assert "금액" in hook.decide(bash(f"({big}) && echo done"), now=NOW)[0]


def test_plugin_without_market_infers_overseas_and_caps_in_usd():
    cmd = f"{PLUGIN} --side buy --symbol AAPL --exchange NASDAQ --qty 100 --order-type limit --price 150"
    [r] = hook.decide(bash(cmd), now=NOW)
    assert "$15,000.00" in r
    [o] = parse.orders_from_event(bash(f"{PLUGIN} --side buy --symbol TSLA --qty 1 --order-type limit --price 1"))
    assert o.market == "overseas"


@pytest.mark.parametrize("cmd", [
    "ls -la && git status",
    'git commit -m "Fix orders table; don\'t double count"',
    "cat <<'EOF'\nThe user's orders are listed\nEOF",
    "grep -rn 'orders' src/ | head",
    "uv run python -m kw_runtime.cli open-orders",
    "uv run python -m kw_runtime.cli order-confirm --ticket-id abc123",   # 프리뷰에서 이미 봤다
    "kog check 'kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type market --confirm'",
])
def test_common_commands_pass(cmd):
    assert hook.decide(bash(cmd), now=NOW) == []


# ------------------------------------------------------------------ 규칙

def test_notional_cap():
    assert hook.decide(bash(f"{PLUGIN} --side buy --symbol 005930 --qty 10 --order-type limit --price 70000"), now=NOW) == []
    [r] = hook.decide(bash(f"{PLUGIN} --side buy --symbol 000660 --qty 100 --order-type limit --price 70000"), now=NOW)
    assert "7,000,000원" in r and "1,000,000원" in r


def test_domestic_modify_is_capped_overseas_modify_blocked(home):
    reasons = hook.decide(bash(f"{PLUGIN} --kind modify --order-id 1 --qty 50000 --price 70000"), now=NOW)
    assert any("수량" in r for r in reasons) and any("금액" in r for r in reasons)
    [r] = hook.decide(bash(f"{PLUGIN} --kind modify --order-id US-9 --market overseas --price 9999"), now=NOW)
    assert "해외 정정" in r
    write_config(home, "allow_overseas_modify = true\n")
    assert hook.decide(bash(f"{PLUGIN} --kind modify --order-id US-9 --market overseas --price 9999"), now=NOW) == []


def test_market_buy_blocked_market_sell_allowed():
    [r] = hook.decide(bash("kiwoomcli domestic orders buy --code 005930 --qty 1 --order-type market --confirm"), now=NOW)
    assert "시장가" in r
    assert hook.decide(bash("kiwoomcli domestic orders sell --code 005930 --qty 1 --order-type market --confirm"),
                       now=NOW) == []


def test_fx_blocked_by_default():
    [r] = hook.decide(bash("kiwoomcli overseas exchange request --amount 1000 --confirm"), now=NOW)
    assert "환전" in r


def test_sell_not_capped_counted_or_deduped(home):
    write_config(home, "max_orders_per_day = 1\n")
    sell = "kiwoomcli domestic orders sell --code 005930 --qty 5000 --order-type limit --price 70000 --confirm"
    for i in range(5):
        assert send(bash(sell), NOW + i) == []
    assert send(buy(), NOW + 10) == []   # 매도는 세지 않았다


def test_duplicate_buy_blocked_only_after_it_ran():
    opts = {"code": "005930", "qty": 1, "order_type": "limit", "price": 70000}
    assert hook.decide(mcp("domestic orders buy", opts), now=NOW) == []     # 사람이 거부했다고 치자(Post 없음)
    assert hook.decide(mcp("domestic orders buy", opts), now=NOW + 5) == []  # 다시 시켜도 통과
    hook.record(mcp("domestic orders buy", opts), now=NOW + 5)              # 이번엔 실행됨
    [r] = hook.decide(mcp("domestic orders buy", opts), now=NOW + 30)
    assert "반복" in r
    assert hook.decide(mcp("domestic orders buy", opts), now=NOW + 200) == []


def test_limits_accumulate_within_one_call(home):
    write_config(home, "max_orders_per_day = 3\nmax_orders_per_code = 2\n")
    one = BUY.format(c="005930", q=1, p=1000)
    reasons = hook.decide(bash(f"{one} && {one}"), now=NOW)
    assert any("반복" in r for r in reasons)
    chain = " && ".join(BUY.format(c=c, q=1, p=1000) for c in ("005930", "000660", "035420", "051910"))
    assert any("하루 상한" in r for r in hook.decide(bash(chain), now=NOW))


def test_daily_and_per_code_limits_and_kst_reset(home):
    write_config(home, "max_orders_per_day = 3\nmax_orders_per_code = 2\nduplicate_window_sec = 0\n")
    assert send(buy(), NOW) == []
    assert send(buy(), NOW + 1) == []
    [r] = send(buy(), NOW + 2)
    assert "종목당" in r
    assert send(buy("000660"), NOW + 3) == []
    [r] = send(buy("035420"), NOW + 4)
    assert "하루 상한" in r
    assert send(buy("035420"), NOW + 86_400) == []


def test_stop_file_blocks_buy_allows_sell_and_cancel(home):
    (home / "STOP").write_text("x")
    assert "비상정지" in hook.decide(buy(), now=NOW)[0]
    assert hook.decide(bash("kiwoomcli domestic orders sell --code 005930 --qty 1 --order-type limit --price 1000 "
                            "--confirm"), now=NOW) == []
    assert hook.decide(bash(f"{PLUGIN} --kind cancel --order-id 0001234"), now=NOW) == []


def test_block_and_white_lists(home):
    write_config(home, 'blocklist = ["005930"]\nwhitelist = ["000660", "005930"]\n')
    assert "차단 목록" in hook.decide(buy("005930"), now=NOW)[0]
    assert "허용 목록" in hook.decide(buy("035420"), now=NOW)[0]
    assert hook.decide(buy("000660"), now=NOW) == []
    sell = "kiwoomcli domestic orders sell --code 005930 --qty 1 --order-type limit --price 1000 --confirm"
    assert "차단 목록" in hook.decide(bash(sell), now=NOW)[0]   # 보유 종목을 에이전트가 팔지 못하게


@pytest.mark.parametrize("text,needle", [
    ("max_notional = 5\n", "알 수 없는 설정 키"),
    ('blocklist = "005930"\n', "형식"),
    ("max_qty = -1\n", "형식"),
    ("allow_market_buy = 1\n", "형식"),
])
def test_bad_config_blocks_orders(home, text, needle):
    write_config(home, text)
    [r] = hook.decide(buy(), now=NOW)
    assert needle in r


def test_missing_config_after_init_blocks(home):
    from kiwoom_order_guard import cli
    assert cli.main(["init"]) == 0
    assert hook.decide(buy(), now=NOW) == []
    (home / "config.toml").unlink()
    assert "사라졌다" in hook.decide(buy(), now=NOW)[0]


# ------------------------------------------------------------------ 가드 자체 보호

@pytest.mark.parametrize("event", [
    {"tool_name": "Write", "tool_input": {"file_path": "~/.config/kiwoom-order-guard/config.toml", "content": "x"}},
    {"tool_name": "Bash", "tool_input": {"command": "rm ~/.config/kiwoom-order-guard/STOP"}},
    {"tool_name": "Bash", "tool_input": {"command": "rm -rf ~/.config/kiwoom-o*"}},
    {"tool_name": "Bash", "tool_input": {"command": "kog resume"}},
    {"tool_name": "Bash", "tool_input": {"command": 'kog "resume"'}},
    {"tool_name": "Bash", "tool_input": {"command": "python -m kiwoom_order_guard.cli resume"}},
    {"tool_name": "Bash", "tool_input": {"command": "claude plugin disable kiwoom-order-guard"}},
    {"tool_name": "Bash", "tool_input": {"command": "jq '.disableAllHooks=true' s.json > t && mv t s.json"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/home/u/.claude/settings.json",
                                         "old_string": '"kiwoom-order-guard@kiwoom-order-guard": true',
                                         "new_string": '"kiwoom-order-guard@kiwoom-order-guard": false'}},
])
def test_agent_cannot_loosen_guard(event):
    [r] = hook.decide(event, now=NOW)
    assert "사람이 직접" in r


def test_expanded_home_path_is_protected(home):
    assert hook.decide(bash(f"sed -i s/1000000/99999999/ {home}/config.toml"), now=NOW)


@pytest.mark.parametrize("event", [
    {"tool_name": "Read", "tool_input": {"file_path": "/x/.config/kiwoom-order-guard/config.toml"}},
    {"tool_name": "Bash", "tool_input": {"command": "kog status && kog stop"}},
    {"tool_name": "Bash", "tool_input": {"command": "cat ~/git/kiwoom-order-guard/README.md | grep uninstall"}},
    {"tool_name": "Edit", "tool_input": {"file_path": "/home/u/git/kiwoom-order-guard/kiwoom_order_guard/rules.py",
                                         "old_string": "a", "new_string": "remove the duplicate"}},
])
def test_guard_protection_has_no_common_false_positives(event):
    assert hook.decide(event, now=NOW) == []


# ------------------------------------------------------------------ 훅 프로세스

def _run(stdin: bytes, home, *args: str) -> subprocess.CompletedProcess:
    env = {"KIWOOM_ORDER_GUARD_HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONIOENCODING": "cp949"}
    return subprocess.run([sys.executable, "-m", "kiwoom_order_guard.hook", *args], check=False, input=stdin,
                          capture_output=True, env=env, cwd=str(ROOT))


def test_hook_exit_codes_and_utf8_stdin_under_cp949_locale(home):
    ev = buy(q=1, p=1000)
    ev["tool_input"]["command"] = BUY.format(c="005930", q=100, p=70000) + "  # 삼성전자 매수"
    blocked = _run(json.dumps(ev, ensure_ascii=False).encode("utf-8"), home)
    assert blocked.returncode == 2 and "kiwoom-order-guard 차단" in blocked.stderr.decode("utf-8")
    assert _run(json.dumps(bash("echo 안녕"), ensure_ascii=False).encode("utf-8"), home).returncode == 0
    assert _run(b"not json", home).returncode == 0
    assert _run(b'{"tool_name": "Bash", "tool_input": {"command": "kiwoomcli ', home).returncode == 2


def test_post_hook_records(home):
    ev = json.dumps(buy(), ensure_ascii=False).encode("utf-8")
    assert _run(ev, home, "post").returncode == 0
    state = json.loads((home / "state" / "state.json").read_text())
    assert state["total"] == 1 and state["by_code"] == {"005930": 1}


def test_non_order_decision_is_fast():
    ev = bash("git log --oneline -20 | head && ls")
    t = time.perf_counter()
    for _ in range(1000):
        hook.decide(ev, now=NOW)
    assert (time.perf_counter() - t) / 1000 < 0.001


def test_default_config_matches_toml():
    import tomllib
    data = tomllib.loads(DEFAULT_TOML)
    default = Config()
    for k, v in data.items():
        got = getattr(default, k)
        assert (sorted(got) if isinstance(got, frozenset) else got) == v, k
    assert set(data) == set(Config.__dataclass_fields__)
