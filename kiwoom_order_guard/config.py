"""설정과 상태 파일 위치.

설정은 사람이 직접 고친다. 에이전트가 이 폴더를 건드리는 도구 호출은 훅이 막는다(:mod:`.hook`).
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

__all__ = ["DEFAULT_TOML", "Config", "config_dir", "load_config", "state_dir"]


def config_dir() -> Path:
    if env := os.environ.get("KIWOOM_ORDER_GUARD_HOME"):
        return Path(env)
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "kiwoom-order-guard"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kiwoom-order-guard"


def state_dir() -> Path:
    return config_dir()


@dataclass(frozen=True)
class Config:
    # 금액·수량 (매수와 정정에 적용. 매도는 보유분 청산을 막지 않도록 기본 제외)
    max_notional_krw: int = 1_000_000      # 국내 지정가 1건 금액 상한(수량×단가)
    max_qty: int = 1_000                   # 1건 수량 상한
    apply_caps_to_sell: bool = False
    # 시장가류(단가 없음)는 금액을 알 수 없다
    allow_market_buy: bool = False
    allow_market_sell: bool = True
    # 반복·횟수 (가드를 통과한 전송·주문표 생성 시도를 센다 — 체결 수가 아니다)
    duplicate_window_sec: int = 120
    max_orders_per_day: int = 20
    max_orders_per_code: int = 5
    # 종목
    blocklist: frozenset[str] = field(default_factory=frozenset)
    whitelist: frozenset[str] = field(default_factory=frozenset)  # 비어 있으면 미적용
    # 해외는 USD 라 금액 상한을 따로 둔다. 0 = 해외 신규 매수 차단
    max_notional_usd: float = 1_000.0


DEFAULT_TOML = """\
# kiwoom-order-guard 설정 — 사람이 직접 고친다. AI 에이전트의 수정은 훅이 막는다.
max_notional_krw = 1000000     # 국내 지정가 1건 금액 상한(원)
max_qty = 1000                 # 1건 수량 상한
apply_caps_to_sell = false     # true 면 매도에도 금액·수량 상한
allow_market_buy = false       # 시장가류 매수 허용 여부(금액을 미리 알 수 없다)
allow_market_sell = true
duplicate_window_sec = 120     # 같은 주문(방향·종목·수량·단가) 반복 차단 시간
max_orders_per_day = 20        # 하루 주문 시도 상한(0 = 끔)
max_orders_per_code = 5        # 종목당 하루 상한(0 = 끔)
max_notional_usd = 1000.0      # 해외 지정가 1건 상한(USD, 0 = 해외 신규 매수 차단)
blocklist = []                 # 항상 차단할 종목코드
whitelist = []                 # 비어 있지 않으면 이 종목만 허용
"""


def load_config(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.toml"
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {f.name: f for f in fields(Config)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        # 오타 난 키를 조용히 무시하면 사람이 건 한도가 안 걸린다.
        raise ValueError(f"알 수 없는 설정 키: {', '.join(unknown)} ({path})")
    kwargs = {}
    for key, value in data.items():
        if key in ("blocklist", "whitelist"):
            value = frozenset(str(v).strip().upper() for v in value)
        kwargs[key] = value
    return Config(**kwargs)
