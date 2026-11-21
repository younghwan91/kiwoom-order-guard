"""오늘 매수 시도 횟수·최근 매수. JSON 파일 하나, 날짜(KST)가 바뀌면 횟수를 비운다.

PreToolUse 는 읽기만 하고, 실제로 실행된 호출만 PostToolUse 가 기록한다 — 사람이 권한 창에서
거부한 주문이 "반복 전송"으로 잡히지 않게.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .rules import Recent

__all__ = ["STOP_FILE", "State", "kst_date", "locked"]

KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없다
STOP_FILE = "STOP"
_KEEP_RECENT_SEC = 3600


def kst_date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, KST).date().isoformat()


@contextlib.contextmanager
def locked(directory: Path) -> Iterator[None]:
    """병렬 도구 호출이 같은 상태를 동시에 고치지 않게 잠근다(가능한 플랫폼에서)."""
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".lock", "a+b") as fh:
        try:
            import fcntl

            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # Windows
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        yield


@dataclass
class State:
    path: Path
    date: str = ""
    total: int = 0
    by_code: dict[str, int] = field(default_factory=dict)
    recent: list[Recent] = field(default_factory=list)

    @classmethod
    def load(cls, directory: Path, now: float) -> State:
        path = directory / "state.json"
        state = cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        today = kst_date(now)
        if raw.get("date") == today:
            state.total = int(raw.get("total", 0))
            state.by_code = {str(k): int(v) for k, v in (raw.get("by_code") or {}).items()}
        state.date = today
        state.recent = [
            Recent(str(r["key"]), float(r["at"]))
            for r in raw.get("recent") or []
            if now - float(r.get("at", 0)) < _KEEP_RECENT_SEC
        ]
        return state

    def record(self, key: str, code: str, now: float) -> None:
        self.total += 1
        if code:
            self.by_code[code] = self.by_code.get(code, 0) + 1
        self.recent.append(Recent(key, now))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "date": self.date,
            "total": self.total,
            "by_code": self.by_code,
            "recent": [{"key": r.key, "at": r.at} for r in self.recent],
        }
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, self.path)
