# kiwoom-order-guard

**AI 에이전트가 키움 공식 MCP·플러그인으로 내는 주문의 _내용_을 전송 전에 검사하는 Claude Code 훅.**
공식 도구는 "사람이 확인했나"를 본다. 이 훅은 "확인할 만한 주문인가"를 본다.

```text
kiwoom-order-guard 차단:
- 주문 금액 7,000,000원(100주×70,000원)이 상한 1,000,000원을 넘음
한도를 바꾸려면 사용자가 직접 설정 파일을 고친다(`kog config`로 위치 확인).
```

## 왜 필요한가

키움은 2026-08~09 에 공식 MCP 서버(`Kiwoom-REST-API/mcp_exec`)와 Claude Code 플러그인(`Kiwoom-Plugin`)을 냈다.
둘 다 주문 **경로**는 잘 지킨다 — 주문 도구는 기본 꺼짐, 프리뷰→사람 확인→전송, 실전 계좌는 명시 승인.
그런데 주문 **내용**은 아무도 보지 않는다. 공식 서버 코드의 주석 그대로:

> confirm can be set by the caller (an AI included). Human approval relies on the client's
> tool-execution approval or a future plugin hook.
> — `mcp_exec/src/kiwoom_exec_mcp/server.py`

에이전트가 주문을 제안하고 사람이 "확인"을 누르는 일이 반복되면 확인은 습관이 된다.
그 습관이 놓치는 것을 이 훅이 잡는다.

| 검사 | 기본값 | 거래소·공식 도구가 막나 |
|---|---|---|
| 1건 금액 상한 (국내 원, 해외 USD) | 100만원 / $1,000 | 아니오 |
| 1건 수량 상한 | 1,000주 | 아니오 |
| 시장가류 매수 (금액을 미리 모름) | 차단 | 아니오 |
| 같은 주문 반복 (에이전트 재시도 루프) | 120초 안 차단 | 아니오 |
| 하루·종목별 주문 시도 횟수 | 20건 / 5건 | 아니오 |
| 차단·허용 종목 목록 | 비어 있음 | 아니오 |
| 비상정지 (`kog stop`) | 꺼짐 | 아니오 |
| 에이전트가 설정을 고치거나 가드를 끄려는 시도 | 차단 | — |

매도는 보유분 청산을 막지 않도록 금액·수량 상한에서 기본 제외한다(`apply_caps_to_sell`). 취소는 위험을 줄이므로 통과한다.
주문처럼 보이는데 종목·수량·단가를 읽지 못하면 **막는다**.

## 설치

Claude Code 에서:

```text
/plugin marketplace add younghwan91/kiwoom-order-guard
/plugin install kiwoom-order-guard@kiwoom-order-guard
```

훅은 `uv` 로 돈다(공식 키움 플러그인과 같은 방식). 의존성은 없다 — 파이썬 표준 라이브러리만 쓴다.

한도는 **직접** 정한다. 터미널에서:

```bash
uvx --from git+https://github.com/younghwan91/kiwoom-order-guard kog init     # 기본 설정 파일
uvx --from git+https://github.com/younghwan91/kiwoom-order-guard kog config   # 위치·내용
```

설정 위치: Linux·macOS `~/.config/kiwoom-order-guard/config.toml`, Windows `%APPDATA%\kiwoom-order-guard\config.toml`.
모르는 키가 있으면 주문을 막는다(오타 난 한도가 조용히 무시되지 않게).

## 검사하는 주문 경로

입력 모양은 공식 자료에서 그대로 가져왔다.

| 경로 | 언제 검사하나 | 출처 |
|---|---|---|
| 플러그인 `python -m kw_runtime.cli order-preview …` | 주문표를 만들 때 (전송은 주문표 ID 만 받는다) | `Kiwoom-Plugin/skills/kiwoom-order/SKILL.md` |
| `kiwoomcli domestic\|overseas orders <buy\|sell\|modify\|cancel\|credit-*\|gold-*> … --confirm` | `--confirm` 이 있을 때 | kwcli 1.0.3 `maps/arguments.csv` |
| MCP `kiwoom_order_submit(command_path, options, confirm=true)` | `confirm` 이 정확히 `true` 일 때 | `mcp_exec/.../server.py` |

## 못 막는 것

이 훅은 안전벨트지 금고가 아니다.

- **훅을 거치지 않는 호출.** 에이전트가 `python -c` 로 REST 를 직접 부르거나, 플러그인 런타임 HTTP 에 직접 `POST` 하거나,
  사람이 Trading Surface 화면에서 직접 누른 주문은 보지 않는다.
- **Claude Code 밖.** 훅은 Claude Code 에서만 돈다. Claude Desktop·Codex·Cursor 의 MCP 호출은 지나간다
  (공식 플러그인의 `prod_guard` 도 같은 한계를 문서에 적어 두었다).
- **시세 기준 검사.** 전일 종가·가격제한폭·현재가 대비 괴리는 v0.1 에 없다. 시세 응답 필드를 실서버에서 확인하기 전에는
  코드에 넣지 않는다.
- **위험 공시 종목.** 거래정지·관리종목·당일 유상증자·CB 공시 종목 차단은 다음 판에서.
- **횟수는 체결이 아니라 시도다.** 플러그인 경로는 주문표를 만들 때 센다 — 만들고 버린 주문표도 1건이다.

## 명령 (`kog`)

| 명령 | 하는 일 |
|---|---|
| `kog init` / `kog config` | 기본 설정 만들기 / 위치·내용 보기 |
| `kog status` | 오늘 시도 횟수, 비상정지 여부 |
| `kog stop` / `kog resume` | 비상정지 켜기 / 풀기 — `resume` 은 에이전트가 못 한다 |
| `kog check '<명령>'` | Bash 명령 하나를 판정만 해 본다(기록 안 함) |

## 개발

```bash
uv sync
uv run pytest -q
```

투자 권유가 아니다. 주문의 책임은 계좌 주인에게 있다. MIT.

---

**채영환 (Younghwan Chae)** · [GitHub @younghwan91](https://github.com/younghwan91)
