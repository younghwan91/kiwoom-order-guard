# 원장

## 2026-11-21 시작 — 완료 조건

키움 공식 MCP(`kiwoom-exec`)·플러그인(`Kiwoom-Plugin`)은 "사람이 확인했나"만 본다(`prod_guard.py`,
`server.py` 의 "confirm can be set by the caller (an AI included)"). 주문 **내용**을 보는 가드를 만든다.

끝 = 아래가 모두 참.
1. 주문 경로 3개를 공식 자료 그대로의 입력으로 해석한다.
   - 플러그인 `python -m kw_runtime.cli order-preview ...` (Kiwoom-Plugin `skills/kiwoom-order/SKILL.md` 예시)
   - `kiwoomcli domestic orders buy|sell|... --confirm` (kwcli 1.0.3 `maps/arguments.csv`: `--code --qty --price --order-type --exchange`)
   - MCP `kiwoom_order_submit(command_path, options, confirm)` (kiwoom-exec `server.py`, `options_to_args` 는 `_`→`-`)
2. 규칙: 비상정지, 차단·허용 목록, 수량 상한, 금액 상한, 시장가 매수, 같은 주문 반복, 하루·종목별 횟수, 설정 파일 변경 차단.
   주문처럼 보이는데 해석이 안 되면 막는다(fail-closed).
3. 규칙마다 테스트. 주문이 아닌 Bash 호출은 빠른 길로 통과(측정값 기록).
4. README 에 못 막는 것(파이썬·HTTP 직접 호출, 해외 주문 금액, 전일 종가 대비 가격) 을 적는다.
5. 독립 코드 리뷰 → MAJOR 반영 후 공개 푸시.

하지 않는 것(v0.1): 실시간 시세 조회(전일 종가·가격제한폭 대비 검사) — 브로커 응답 필드는 실서버 스모크 전에는 코드에 넣지 않는다.
DART 위험 공시 게이트 — v0.2.
