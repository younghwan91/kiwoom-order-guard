# 기여 가이드

kiwoom-order-guard 에 관심 가져 주셔서 감사합니다. 이 훅은 AI 에이전트가 내는 **실주문**을 막는 안전장치라,
검사를 약하게 만드는 변경은 받지 않습니다. 버그 제보, 문서 수정, 검사 규칙 추가는 환영합니다.
처음이라면 [good first issue](https://github.com/younghwan91/kiwoom-order-guard/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) 목록에서 시작하세요.

> English: PRs and issues in English are welcome. Short version: `uv run pytest -q` and `uv run ruff check .` must pass;
> never use real keys, accounts or live orders; changes must not weaken a check; use English Conventional Commits.

## 개발 환경

Python 3.11 이상과 [uv](https://docs.astral.sh/uv/) 가 필요합니다.

```bash
git clone https://github.com/younghwan91/kiwoom-order-guard.git
cd kiwoom-order-guard
uv sync                      # 개발 의존성(pytest, ruff)까지 설치합니다
```

## 테스트와 린트

```bash
uv run pytest -q             # 1초 안팎, 키·계좌·네트워크 없이 돕니다
uv run ruff check .          # CI 는 최신 ruff 를 씁니다
```

- 테스트는 시뮬레이션 주문(`tests/fixtures/`)만 씁니다. 실제 키움 서버로 요청하지 않습니다.
- 앱키·시크릿·토큰·계좌번호를 커밋·이슈·PR·픽스처에 넣지 마세요.
- 검사 규칙을 더하거나 바꾸면, 막아야 할 주문과 통과해야 할 주문을 모두 테스트로 넣어 주세요.
- 한도·기본값을 느슨하게 하거나 검사를 우회하는 변경은 받지 않습니다. 가드는 더하는 쪽이 기본입니다.

## 커밋과 PR

1. `main` 에서 브랜치를 만듭니다(`feat/…`, `fix/…`, `docs/…`).
2. 커밋 메시지는 **영어 [Conventional Commits](https://www.conventionalcommits.org/)** 입니다(`feat(rules): add per-symbol cap`). 본문에는 왜 바꿨는지 적어 주세요.
3. 위 두 명령이 통과하는지 확인한 뒤 PR 을 엽니다. 작고 한 가지 일만 하는 PR 이 빨리 머지됩니다.
4. CI(ruff·pytest)가 통과해야 머지됩니다.

## 이슈

- 버그·기능 제안: [Issues](https://github.com/younghwan91/kiwoom-order-guard/issues)
- good first issue: [목록 보기](https://github.com/younghwan91/kiwoom-order-guard/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
