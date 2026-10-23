"""실행 진입점: 수집 → 새 항목만 추리기 → 텔레그램 전송 → 상태 저장.

사용법:
    python -m bot.main            # 실제 전송
    python -m bot.main --dry-run  # 전송·저장 없이 결과만 출력
    python -m bot.main --test     # 테스트 메시지 한 건만 전송
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import yaml

from .notifier import Telegram, TelegramError, format_item
from .sources import FETCHERS, Item
from .store import Store


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def mark_urgent(items: list[Item], keywords: list[str]) -> None:
    kws = [k.lower() for k in keywords]
    for it in items:
        if it.category == "security" and not it.urgent:
            if any(k in it.title.lower() for k in kws):
                it.urgent = True


def collect(cfg: dict, store: Store, log=print) -> tuple[list[Item], int, int]:
    """모든 소스를 돌며 새 항목을 모은다. 처음 보는 소스는 조용히 시드만 한다."""
    new_items: list[Item] = []
    ok = failed = 0
    seen_in_run: set[str] = set()
    first_run_preview = max(0, int(cfg.get("first_run_preview", 0)))
    for src in cfg["sources"]:
        fetch = FETCHERS.get(src["type"])
        if fetch is None:
            log(f"[skip] {src['id']}: unknown type {src['type']}")
            continue
        try:
            fetched = fetch(src)
        except Exception as exc:  # 한 소스의 실패가 전체를 막지 않게
            failed += 1
            log(f"[fail] {src['id']}: {type(exc).__name__}: {exc}")
            continue
        ok += 1
        for it in fetched:
            it.src_id = src["id"]
        if src["id"] not in store.seeded:
            # 처음 붙인 소스: 최신 N건은 미리보기로 보내고, 나머지는 기존 글로만 기록
            preview = fetched[:first_run_preview]
            for it in fetched[first_run_preview:]:
                store.add(it.id)
            store.seeded.add(src["id"])
            seen_in_run.update(i.id for i in preview)
            new_items.extend(preview)
            log(
                f"[seed] {src['id']}: 미리보기 {len(preview)}건 전송, "
                f"{len(fetched) - len(preview)}건은 기존 글로 기록"
            )
            continue
        fresh = []
        for it in fetched:
            if store.has(it.id) or it.id in seen_in_run:
                continue
            seen_in_run.add(it.id)
            fresh.append(it)
        log(f"[ok]   {src['id']}: 전체 {len(fetched)}건 / 새 글 {len(fresh)}건")
        new_items.extend(fresh)
    return new_items, ok, failed


def deliver(items, send, store: Store, limit: int, log=print) -> int:
    """긴급 우선으로 최대 limit건 전송. 성공한 것만 기록하고, 실패·초과분은 다음 실행에 다시 시도."""
    items = sorted(items, key=lambda i: not i.urgent)
    sent = 0
    for it in items[:limit]:
        try:
            send(format_item(it))
        except TelegramError as exc:
            log(f"[send-fail] {it.id}: {exc}")
            break
        store.add(it.id)
        sent += 1
        time.sleep(1)  # 텔레그램 속도 제한 완화
    rest = len(items) - sent
    if rest > 0:
        log(f"[defer] {rest}건은 다음 실행으로 넘김")
    return sent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args(argv)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    tg = Telegram(token, chat_id) if token and chat_id else None

    if args.test:
        if tg is None:
            print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 필요합니다.")
            return 2
        tg.send("✅ news-bot 테스트 메시지입니다. 전송이 정상 동작해요.")
        print("테스트 메시지를 보냈습니다.")
        return 0

    if tg is None and not args.dry_run:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 필요합니다.")
        return 2

    cfg = load_config()
    store = Store("state/seen.json")
    items, ok, failed = collect(cfg, store)
    mark_urgent(items, cfg.get("urgent_keywords", []))

    if args.dry_run:
        for it in sorted(items, key=lambda i: not i.urgent):
            print(("🚨 " if it.urgent else "• ") + f"[{it.category}] {it.title}  {it.url}")
        print(f"dry-run: 새 글 {len(items)}건 (전송·저장 안 함)")
        return 0

    sent = deliver(items, tg.send, store, cfg.get("max_items_per_run", 20))
    store.save()
    print(f"완료: 소스 성공 {ok} / 실패 {failed}, 전송 {sent}건")
    return 1 if ok == 0 and failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
