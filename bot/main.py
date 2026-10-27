"""실행 진입점: 수집 → 새 항목만 추리기 → 텔레그램 전송 → 상태 저장.

사용법:
    python -m bot.main            # 실제 전송
    python -m bot.main --dry-run  # 전송·저장 없이 결과만 출력
    python -m bot.main --test     # 테스트 메시지 한 건만 전송
"""
from __future__ import annotations

import argparse
import html
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yaml

from .notifier import Telegram, TelegramError, format_item
from .sources import FETCHERS, Item
from .store import Store
from .articles import enrich
from .summarizer import DEFAULT_MODEL, summarize, triage


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
    default_preview = max(0, int(cfg.get("first_run_preview", 0)))
    preview_budget = max(0, int(cfg.get("first_run_preview_max", 10)))  # 미리보기 전체 상한

    def run_fetch(src: dict):
        fetch = FETCHERS.get(src["type"])
        if fetch is None:
            return src, None, f"unknown type {src['type']}"
        try:
            return src, fetch(src), None
        except Exception as exc:  # 한 소스의 실패가 전체를 막지 않게
            return src, None, f"{type(exc).__name__}: {exc}"

    # 소스가 많아도 느린 사이트 하나에 발목 잡히지 않도록 병렬로 가져오고, 처리는 순서대로 한다.
    workers = max(1, int(cfg.get("fetch_workers", 8)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run_fetch, cfg["sources"]))

    for src, fetched, err in results:
        if err is not None:
            failed += 1
            log(f"[fail] {src['id']}: {err}")
            continue
        ok += 1
        for it in fetched:
            it.src_id = src["id"]
        if src["id"] not in store.seeded:
            # 처음 붙인 소스: 최신 N건은 미리보기로 보내고, 나머지는 기존 글로만 기록
            want = max(0, int(src.get("first_run_preview", default_preview)))
            n = min(want, preview_budget)
            preview_budget -= n
            preview = fetched[:n]
            for it in fetched[n:]:
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


def prepare(
    items: list[Item], cfg: dict, store: Store, log=print, notes: list[str] | None = None
) -> list[Item]:
    """Claude가 중요한 글만 고르고(triage) 요약한다(summarize). 보낼 글이 없으면 빈 목록.

    - 기준 점수(min_importance) 미만은 다시 판단하지 않도록 '본 것'으로 기록하고 버린다.
    - 기준 이상이 max_picks 를 넘으면 점수 높은 순으로 자르고, 나머지는 기록하지 않아 다음 실행에서 다시 잡힌다.
    - 평가 자체가 실패하면(키 오류·크레딧 소진 등) 아무것도 기록하지 않고 다음 실행으로 넘긴다.
      긴급(🚨) 글만 요약 없이 보내고, 원인은 notes 에 넣는다.
    """
    items = sorted(items, key=lambda i: not i.urgent)
    scfg = cfg.get("summarizer") or {}
    if not scfg.get("enabled"):
        return items
    model = scfg.get("model", DEFAULT_MODEL)
    min_importance = int(scfg.get("min_importance", 7))
    max_picks = int(scfg.get("max_picks", 10))
    # 한 번에 평가할 양을 제한. 넘친 항목은 기록하지 않으므로 다음 실행에서 다시 잡힌다.
    cands = items[: int(scfg.get("max_candidates", 150))]
    if len(items) > len(cands):
        log(f"[defer] 평가 한도를 넘은 {len(items) - len(cands)}건은 다음 실행으로 넘김")

    err = triage(cands, model=model, chunk_size=int(scfg.get("triage_batch", 50)), log=log)
    if err and notes is not None:
        notes.append(err)

    scored = [i for i in cands if i.importance is not None]
    if not scored:  # 평가 실패: 긴급 글만 보내고 나머지는 다음 실행에서 다시 평가
        picks = [i for i in cands if i.urgent]
    else:
        for it in scored:
            if it.importance < min_importance:
                store.add(it.id)
                log(f"[drop] {it.id}: 중요도 {it.importance}")
        picks = sorted(
            (i for i in scored if i.importance >= min_importance),
            key=lambda i: (-i.importance, not i.urgent),
        )
        if len(picks) > max_picks:
            log(f"[defer] 중요한 글 {len(picks) - max_picks}건은 다음 실행으로 넘김")
            picks = picks[:max_picks]
    log(f"[pick] 평가 {len(scored)}/{len(cands)}건 → 전송 대상 {len(picks)}건")
    if not picks:
        return []

    got = enrich(picks, log=log)
    log(f"[enrich] 원문 보강 {got}건")
    err = summarize(picks, model=model, batch_size=int(scfg.get("summary_batch", 8)), log=log)
    if err and notes is not None and err not in notes:
        notes.append(err)
    return picks


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


SUMMARY_WARN_INTERVAL = 5 * 3600  # 같은 경고는 5시간에 한 번만 (6시간 주기에서 매번 한 번씩)


def warn_summary(send, store: Store, notes: list[str], log=print) -> None:
    """요약이 실패했으면 이유를 텔레그램으로 한 번 알린다(자주 반복되지 않게 제한)."""
    if not notes:
        return
    log(f"[summary-warn] {notes[0]}")
    now = int(time.time())
    if now - int(store.meta.get("summary_warn_at", 0)) < SUMMARY_WARN_INTERVAL:
        return
    try:
        send(f"⚠️ 요약 기능을 쓰지 못했어요: {html.escape(notes[0])}\n알림은 요약 없이 그대로 전송됩니다.")
        store.meta["summary_warn_at"] = now
    except TelegramError as exc:
        log(f"[send-fail] 경고 전송 실패: {exc}")


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
        scfg = load_config().get("summarizer") or {}
        if scfg.get("enabled"):
            # 요약 연결도 한 건으로 점검해서 결과를 텔레그램으로 알려 준다
            probe = Item(
                id="probe",
                title="curl 8.23.0",
                url="https://example.com",
                source="test",
                category="security",
                body="curl 8.23.0 fixes an out-of-bounds read in HTTP/2 handling and several minor bugs.",
            )
            err = summarize([probe], model=scfg.get("model", DEFAULT_MODEL))
            if probe.summary and not err:
                tg.send(f"✅ 요약 연결 정상: {html.escape(probe.summary)}")
            else:
                tg.send(f"⚠️ 요약 기능을 쓰지 못했어요: {html.escape(err or '응답이 비어 있음')}")
        print("테스트 메시지를 보냈습니다.")
        return 0

    if tg is None and not args.dry_run:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 필요합니다.")
        return 2

    cfg = load_config()
    store = Store("state/seen.json")
    items, ok, failed = collect(cfg, store)
    mark_urgent(items, cfg.get("urgent_keywords", []))
    notes: list[str] = []
    items = prepare(items, cfg, store, notes=notes)

    if args.dry_run:
        for it in items:
            line = ("🚨 " if it.urgent else "• ") + f"[{it.label or it.category}] {it.title}  {it.url}"
            print(line + (f"\n    └ {it.summary}" if it.summary else ""))
        for note in notes:
            print(f"⚠ 요약 실패: {note}")
        print(f"dry-run: 새 글 {len(items)}건 (전송·저장 안 함)")
        return 0

    sent = deliver(items, tg.send, store, cfg.get("max_items_per_run", 20))
    warn_summary(tg.send, store, notes)
    store.save()
    print(f"완료: 소스 성공 {ok} / 실패 {failed}, 전송 {sent}건")
    return 1 if ok == 0 and failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
