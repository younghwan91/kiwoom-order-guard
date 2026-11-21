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

## 2026-11-21 독립 리뷰 1차 → 반영
CRITICAL 4(해외 원화 오계산·한 호출 안 반복·래퍼 통과·cp949 stdin)·MAJOR 7 전부 재현됨 → 해석기 재작성.
- 탐지는 넓게(원문 정규식, 공식 prod_guard 방식), 해석은 좁게. 원문의 도구 언급 수 > 해석한 호출 수면 막음.
- 플러그인 플래그 화이트리스트(argparse allow_abbrev), 종목은 코드 모양만, --market 없으면 거래소·종목으로 추론.
- 매도는 횟수·반복·상한 제외(차단 목록만 양방향). 기록은 PostToolUse. 해외 정정·환전 기본 차단.
- 설정 타입 검사, 초기화 표시(상태 폴더)로 설정 삭제 감지, 상태 파일 잠금.
리뷰어 재현 스크립트 재실행: 우회 전부 막힘. 남은 것: `mv` 로 설정 폴더 옮기기(초기화했으면 표시로 막힘).
테스트 63개 통과. 훅 실행 45ms(uv 기동, Linux).

## 2026-11-21 독립 리뷰 2차 → 반영
CRITICAL 2(실행 파일 변수·따옴표 쪼개기, kog check "$( )")·MAJOR 4(함수·find -exec, 스크립트 파일, 따옴표 안 for 오탐, 금 코드·설정 오류 때 취소 막힘).
- 탐지를 실행 파일 이름에서 명령 구조로: 따옴표·백슬래시를 지운 글에서 `orders <쓰기> … --confirm`, 구간 해석도 실행 파일 무관.
- 반복 판정은 토큰 자리(구간 첫 토큰, xargs/-exec 등), 함수 정의 차단. 글자만 다루는 명령(grep·echo·git…)의 따옴표 인자는 예외, $( )·백틱은 아님.
- 편집 도구는 대상 경로만, settings.json 은 새로 쓰는 글만 본다. 주문 든 스크립트 파일 쓰기 차단.
- 설정 오류 때 취소·매도 통과, 상태 폴더 쓰기 불가면 매수 차단, Post 기록 실패 경고.
리뷰어 probe3 재실행: 우회 전부 막힘(이미 있는 스크립트 실행만 남음 — README). 테스트 86개.
