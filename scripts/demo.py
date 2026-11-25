"""README 데모 그림을 만든다: 가짜(시뮬레이션) 주문 하나를 실제 훅에 넣고 그 출력을 SVG 로 저장한다.

    uv run --no-project --python 3.13 --with rich python scripts/demo.py

키·계좌·네트워크는 쓰지 않는다. 입력은 tests/fixtures/simulated_buy_over_cap.json.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from rich.console import Console
from rich.text import Text

ROOT = Path(__file__).parents[1]
FIXTURE = "tests/fixtures/simulated_buy_over_cap.json"
CMD = f"python -m kiwoom_order_guard.hook < {FIXTURE}"


def main() -> int:
    with tempfile.TemporaryDirectory() as home:
        env = {**os.environ, "KIWOOM_ORDER_GUARD_HOME": home, "PYTHONIOENCODING": "utf-8"}
        subprocess.run([sys.executable, "-m", "kiwoom_order_guard.cli", "init"], cwd=ROOT, env=env,
                       check=True, capture_output=True)
        with open(ROOT / FIXTURE, "rb") as stdin:
            run = subprocess.run([sys.executable, "-m", "kiwoom_order_guard.hook"], check=False, cwd=ROOT, env=env,
                                 stdin=stdin, capture_output=True, text=True, encoding="utf-8")
    if run.returncode != 2:
        print(f"expected the guard to block (exit 2), got {run.returncode}", file=sys.stderr)
        return 1

    console = Console(record=True, width=86, force_terminal=True, color_system="truecolor")
    console.print(Text("# 시뮬레이션 주문: AI 가 005930 100주 × 70,000원 지정가 매수를 보냄 (실제 전송 없음)",
                       style="dim"))
    console.print(Text("$ ", style="green") + Text(CMD, style="bold"))
    for i, line in enumerate(run.stderr.rstrip().splitlines()):
        console.print(Text(line, style="bold red" if i == 0 else ("yellow" if line.startswith("- ") else "")))
    console.print(Text(f"exit {run.returncode}  → Claude Code 가 도구 호출을 막고 사유를 에이전트에게 돌려줌",
                       style="dim"))
    out = ROOT / "docs/images/demo-block.svg"
    console.save_svg(str(out), title="kiwoom-order-guard — simulated order")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
