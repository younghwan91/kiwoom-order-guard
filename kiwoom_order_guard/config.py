"""설정과 상태 파일 위치.

설정은 사람이 직접 고친다. 에이전트가 이 폴더를 건드리는 도구 호출은 훅이 막는다(:mod:`.hook`).
상태(횟수·최근 주문·초기화 표시)는 설정과 다른 폴더에 둔다 — 설정 폴더를 통째로 옮겨도
"한 번 초기화했는데 설정이 없다"는 걸 알아채고 주문을 막는다.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

__all__ = ["DEFAULT_TOML", "INIT_MARKER", "Config", "config_dir", "load_config", "state_dir"]

INIT_MARKER = "initialized"


def config_dir() -> Path:
    if env := os.environ.get("KIWOOM_ORDER_GUARD_HOME"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "kiwoom-order-guard"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kiwoom-order-guard"


def state_dir() -> Path:
    if env := os.environ.get("KIWOOM_ORDER_GUARD_HOME"):
        return Path(env) / "state"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "kiwoom-order-guard" / "state"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "kiwoom-order-guard"


@dataclass(frozen=True)
class Config:
    # 금액·수량 (매수와 정정에 적용. 매도는 보유분 청산을 막지 않도록 기본 제외)
    max_notional_krw: int = 1_000_000      # 국내 지정가 1건 금액 상한(수량×단가)
    max_qty: int = 1_000                   # 1건 수량 상한
    apply_caps_to_sell: bool = False
    # 시장가류(단가 없음)는 금액을 알 수 없다
    allow_market_buy: bool = False
    allow_market_sell: bool = True
    # 반복·횟수 — 매수만 센다. 실제로 실행된 호출만 기록한다(PostToolUse). 체결 수가 아니다
    duplicate_window_sec: int = 120
    max_orders_per_day: int = 20
    max_orders_per_code: int = 5
    # 종목. blocklist 는 매도에도 건다(보유 종목을 에이전트가 팔지 못하게). whitelist 는 매수만
    blocklist: frozenset[str] = field(default_factory=frozenset)
    whitelist: frozenset[str] = field(default_factory=frozenset)
    # 해외는 USD. 0 = 해외 신규 매수 차단
    max_notional_usd: float = 1_000.0
    # 가드가 금액을 읽지 못하는 쓰기 명령
    allow_overseas_modify: bool = False    # 해외 정정은 잔량 전체 단가가 바뀌는데 수량을 모른다
    allow_fx: bool = False                 # 환전(overseas exchange request)


DEFAULT_TOML = """\
# kiwoom-order-guard 설정 — 사람이 직접 고친다. AI 에이전트의 수정은 훅이 막는다.
max_notional_krw = 1000000     # 국내 지정가 1건 금액 상한(원)
max_qty = 1000                 # 1건 수량 상한
apply_caps_to_sell = false     # true 면 매도에도 금액·수량 상한
allow_market_buy = false       # 시장가류 매수 허용 여부(금액을 미리 알 수 없다)
allow_market_sell = true
duplicate_window_sec = 120     # 같은 매수(종목·수량·단가) 반복 차단 시간(0 = 끔)
max_orders_per_day = 20        # 하루 매수 시도 상한(0 = 끔)
max_orders_per_code = 5        # 종목당 하루 매수 시도 상한(0 = 끔)
max_notional_usd = 1000.0      # 해외 지정가 1건 상한(USD, 0 = 해외 신규 매수 차단)
allow_overseas_modify = false  # 해외 정정 허용
allow_fx = false               # 환전 허용
blocklist = []                 # 항상 차단할 종목코드(매수·매도 모두)
whitelist = []                 # 비어 있지 않으면 이 종목만 매수 허용
"""


def _coerce(name: str, value: object, default: object, path: Path) -> object:
    def bad() -> ValueError:
        return ValueError(f"{name} 의 형식이 틀림({type(value).__name__}) — {path}")

    if isinstance(default, frozenset):
        if not isinstance(value, list) or not all(isinstance(v, str | int) for v in value):
            raise bad()
        return frozenset(str(v).strip().upper() for v in value)
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise bad()
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise bad()
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
            raise bad()
        return float(value)
    raise bad()


def load_config(path: Path | None = None) -> Config:
    """없으면 기본값. 오타·형식 오류는 ValueError — 조용히 무시하면 사람이 건 한도가 안 걸린다."""
    path = path or config_dir() / "config.toml"
    if not path.exists():
        if (state_dir() / INIT_MARKER).exists():
            raise ValueError(f"설정 파일이 사라졌다({path}) — 사람이 `kog init` 으로 다시 만든다")
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    default = Config()
    known = {f.name for f in fields(Config)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"알 수 없는 설정 키: {', '.join(unknown)} ({path})")
    return Config(**{k: _coerce(k, v, getattr(default, k), path) for k, v in data.items()})
