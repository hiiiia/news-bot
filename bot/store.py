"""보낸 항목 기록(state/seen.json) 관리.

Actions 실행은 매번 새 컨테이너라서, 이 파일을 저장소에 커밋해 상태를 이어간다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

KEEP_SECONDS = 45 * 24 * 3600  # 45일 지난 기록은 정리


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.ids: dict[str, int] = {}
        self.seeded: set[str] = set()
        self.meta: dict = {}  # 경고 시각 등 소소한 상태
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
            self.ids = dict(data.get("ids", {}))
            self.seeded = set(data.get("seeded", []))
            self.meta = dict(data.get("meta", {}))

    def has(self, item_id: str) -> bool:
        return item_id in self.ids

    def add(self, item_id: str) -> None:
        self.ids[item_id] = int(time.time())

    def save(self) -> None:
        cutoff = int(time.time()) - KEEP_SECONDS
        self.ids = {k: v for k, v in self.ids.items() if v >= cutoff}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seeded": sorted(self.seeded),
            "meta": self.meta,
            "ids": dict(sorted(self.ids.items())),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
