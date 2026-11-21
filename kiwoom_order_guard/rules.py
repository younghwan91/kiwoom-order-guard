"""판정. 순수 함수 — 상태(오늘 횟수·최근 주문)와 시계는 호출부가 넘긴다."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .config import Config
from .parse import Order

__all__ = ["Recent", "counts_toward_limits", "evaluate", "order_key"]


@dataclass(frozen=True)
class Recent:
    key: str
    at: float  # epoch seconds


def order_key(order: Order) -> str:
    price = "" if order.price is None else f"{order.price:g}"
    return f"{order.market}|{order.kind}|{order.side}|{order.code.upper()}|{order.qty}|{price}|{order.order_type}"


def counts_toward_limits(order: Order) -> bool:
    """횟수·반복 검사 대상: 신규 매수만. 매도는 급락 때 청산을 막지 않도록 뺀다."""
    return order.kind == "new" and order.side == "buy"


def evaluate(
    order: Order,
    config: Config,
    *,
    now: float,
    stopped: bool,
    orders_today: int,
    orders_by_code: Mapping[str, int],
    recent: Sequence[Recent],
) -> list[str]:
    """막을 사유 목록. 비면 통과. 취소는 위험을 줄이므로 보지 않는다."""
    if order.kind == "cancel":
        return []
    if order.kind == "fx":
        return [] if config.allow_fx else ["환전은 가드가 금액을 읽지 않아 막는다(설정 allow_fx)"]

    reasons: list[str] = []
    code = order.code.upper()
    adds_risk = order.side == "buy" or order.kind == "modify"
    capped = adds_risk or config.apply_caps_to_sell

    if stopped and adds_risk:
        reasons.append("비상정지 중 — 매수·정정을 막는다 (사람이 터미널에서 `kog resume`)")
    if order.kind == "modify" and order.market == "overseas" and not config.allow_overseas_modify:
        reasons.append("해외 정정은 수량을 몰라 금액을 잴 수 없어 막는다(설정 allow_overseas_modify)")

    if code and code in config.blocklist:
        reasons.append(f"차단 목록 종목 — {code}")
    if config.whitelist and order.side == "buy" and code not in config.whitelist:
        reasons.append(f"허용 목록 밖 종목 — {code}")

    if capped and order.qty > config.max_qty:
        reasons.append(f"수량 {order.qty:,}주가 상한 {config.max_qty:,}주를 넘음")

    if order.price is None:
        if order.kind == "new" and order.side == "buy" and not config.allow_market_buy:
            reasons.append(f"시장가류 매수({order.order_type or '단가 없음'})는 금액을 미리 알 수 없어 막는다 — 지정가로 다시")
        if order.kind == "new" and order.side == "sell" and not config.allow_market_sell:
            reasons.append(f"시장가류 매도({order.order_type or '단가 없음'})는 설정에서 꺼져 있다")
    elif capped:
        notional = order.notional or 0.0
        if order.market == "domestic" and notional > config.max_notional_krw:
            reasons.append(
                f"주문 금액 {notional:,.0f}원({order.qty:,}주×{order.price:,.0f}원)이 상한 {config.max_notional_krw:,}원을 넘음"
            )
        if order.market == "overseas" and order.kind == "new" and notional > config.max_notional_usd:
            reasons.append(f"주문 금액 ${notional:,.2f}가 해외 상한 ${config.max_notional_usd:,.2f}를 넘음")

    if counts_toward_limits(order):
        key = order_key(order)
        window = config.duplicate_window_sec
        if window > 0:
            for r in recent:
                if r.key == key and now - r.at < window:
                    reasons.append(f"같은 매수를 {now - r.at:.0f}초 전에 이미 냈다 — 반복 전송 의심")
                    break
        if config.max_orders_per_day and orders_today >= config.max_orders_per_day:
            reasons.append(f"오늘 매수 시도 {orders_today}건 — 하루 상한 {config.max_orders_per_day}건")
        if config.max_orders_per_code and code and orders_by_code.get(code, 0) >= config.max_orders_per_code:
            reasons.append(f"{code} 오늘 {orders_by_code[code]}건 — 종목당 상한 {config.max_orders_per_code}건")

    return reasons
