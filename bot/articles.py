"""요약 재료가 빈약한 글은 원문 페이지에서 본문 텍스트를 가져온다 (best-effort).

피드 본문이 비어 있으면 Claude가 제목만 보고 요약할 수밖에 없어서 요약이 빠지거나 부실해진다.
가져온 텍스트는 외부 입력이므로 요약기에서 '데이터로만' 취급한다.
실패해도 예외를 밖으로 내지 않는다.
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests

MAX_BYTES = 400_000
MAX_REDIRECTS = 3
HEADERS = {"User-Agent": "news-bot/1.0 (personal news digest)"}

_STRIP = re.compile(r"<(script|style|noscript|svg|nav|header|footer|form)\b.*?</\1\s*>", re.S | re.I)
_P = re.compile(r"<p\b[^>]*>(.*?)</p\s*>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_META = re.compile(r"<meta\b[^>]*>", re.I)
_WS = re.compile(r"\s+")


def _text(fragment: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", fragment))).strip()


def _public_host(host: str) -> bool:
    """사설·루프백·링크로컬 주소로 향하는 URL은 가져오지 않는다."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    try:
        return bool(infos) and all(
            ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos
        )
    except ValueError:
        return False


def _safe_url(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.hostname) and _public_host(p.hostname)


def extract_text(page: str, limit: int = 3000) -> str:
    """HTML에서 메타 설명과 본문 문단을 뽑는다."""
    desc = ""
    for m in _META.finditer(page):
        tag = m.group(0)
        if re.search(r'(?:name|property)=["\'](?:og:)?description["\']', tag, re.I):
            c = re.search(r'content=["\']([^"\']*)["\']', tag, re.I)
            if c:
                desc = html.unescape(c.group(1)).strip()
                break
    cleaned = _STRIP.sub(" ", page)
    paras = [t for t in (_text(p) for p in _P.findall(cleaned)) if len(t) > 40]
    body = " ".join(paras) if paras else _text(cleaned)
    return _WS.sub(" ", f"{desc} {body}").strip()[:limit]


def fetch_text(url: str, limit: int = 3000) -> str:
    """URL의 본문 텍스트를 돌려준다. 못 가져오면 빈 문자열."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _safe_url(url):
            return ""
        r = requests.get(url, headers=HEADERS, timeout=10, stream=True, allow_redirects=False)
        try:
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location")
                if not loc:
                    return ""
                url = urljoin(url, loc)
                continue
            if r.status_code != 200 or "html" not in r.headers.get("Content-Type", "").lower():
                return ""
            data = r.raw.read(MAX_BYTES, decode_content=True)
            return extract_text(data.decode(r.encoding or "utf-8", errors="replace"), limit)
        finally:
            r.close()
    return ""


def enrich(items, min_len: int = 400, log=print, workers: int = 4, fetch=None) -> int:
    """본문이 짧은 항목의 body 에 원문 텍스트를 덧붙인다. 보강한 건수를 돌려준다."""
    fetch = fetch or fetch_text
    thin = [it for it in items if len(it.body) < min_len and it.url]

    def one(it):
        try:
            return it, fetch(it.url)
        except Exception as exc:  # 한 글의 실패가 전체를 막지 않게
            log(f"[article-fail] {it.id}: {type(exc).__name__}")
            return it, ""

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for it, text in pool.map(one, thin):
            if text:
                it.body = f"{it.body} {text}".strip()
                done += 1
    return done
