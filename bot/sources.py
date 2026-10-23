"""소스별 수집기. 각 함수는 설정 dict를 받아 Item 리스트를 돌려준다."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

import feedparser
import requests

UA = "news-bot/1.0"
TIMEOUT = 20


@dataclass
class Item:
    id: str
    title: str
    url: str
    source: str
    category: str  # security | it | ai
    urgent: bool = False
    extra: str = ""
    src_id: str = ""  # 어떤 소스 설정에서 왔는지 (main에서 채움)


def _get(url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": UA, **kwargs.pop("headers", {})}
    resp = requests.get(url, headers=headers, timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


def fetch_kev(cfg: dict) -> list[Item]:
    """CISA KEV: 실제 악용이 확인된 취약점. 등재되면 무조건 긴급."""
    data = _get(cfg["url"]).json()
    vulns = sorted(
        data.get("vulnerabilities", []),
        key=lambda v: v.get("dateAdded", ""),
        reverse=True,
    )[: cfg.get("limit", 20)]
    items = []
    for v in vulns:
        cve = v["cveID"]
        name = v.get("vulnerabilityName", "")
        vendor = f"{v.get('vendorProject', '')} {v.get('product', '')}".strip()
        items.append(
            Item(
                id=f"kev:{cve}",
                title=f"{cve} · {vendor} — {name}",
                url=f"https://nvd.nist.gov/vuln/detail/{cve}",
                source="CISA KEV",
                category="security",
                urgent=True,
                extra="실제 악용 확인",
            )
        )
    return items


def fetch_ghsa(cfg: dict) -> list[Item]:
    """GitHub Advisory Database (검증된 권고문)."""
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = _get(
        "https://api.github.com/advisories",
        headers=headers,
        params={
            "per_page": cfg.get("limit", 50),
            "sort": "published",
            "direction": "desc",
            "type": "reviewed",
        },
    ).json()
    wanted = set(cfg.get("severities", ["critical", "high"]))
    items = []
    for a in data:
        sev = (a.get("severity") or "").lower()
        if sev not in wanted:
            continue
        pkgs = []
        for vuln in a.get("vulnerabilities") or []:
            pkg = vuln.get("package") or {}
            if pkg.get("name"):
                pkgs.append(f"{pkg.get('ecosystem', '')}/{pkg['name']}")
        cve = a.get("cve_id") or ""
        items.append(
            Item(
                id=f"ghsa:{a['ghsa_id']}",
                title=f"{cve} {a.get('summary', '')}".strip(),
                url=a["html_url"],
                source="GitHub Advisory",
                category="security",
                urgent=sev == "critical",
                extra=f"{sev}" + (f" · {', '.join(pkgs[:2])}" if pkgs else ""),
            )
        )
    return items


def fetch_nvd(cfg: dict) -> list[Item]:
    """NVD 신규 CVE. CVSS 점수가 붙은 것 중 기준 이상만."""
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(hours=cfg.get("lookback_hours", 3))

    def fmt(d: dt.datetime) -> str:
        return d.strftime("%Y-%m-%dT%H:%M:%S.000") + "+00:00"

    data = _get(
        "https://services.nvd.nist.gov/rest/json/cves/2.0",
        params={"pubStartDate": fmt(start), "pubEndDate": fmt(now)},
    ).json()
    min_cvss = cfg.get("min_cvss", 8.0)
    urgent_cvss = cfg.get("urgent_cvss", 9.0)
    items = []
    for v in data.get("vulnerabilities", []):
        cve = v["cve"]
        score = None
        for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30"):
            metrics = cve.get("metrics", {}).get(key)
            if metrics:
                score = metrics[0]["cvssData"].get("baseScore")
                break
        if score is None or score < min_cvss:
            continue
        desc = next(
            (d["value"] for d in cve.get("descriptions", []) if d.get("lang") == "en"),
            "",
        )
        items.append(
            Item(
                id=f"nvd:{cve['id']}",
                title=f"{cve['id']} — {desc[:160]}",
                url=f"https://nvd.nist.gov/vuln/detail/{cve['id']}",
                source="NVD",
                category="security",
                urgent=score >= urgent_cvss,
                extra=f"CVSS {score}",
            )
        )
    return items


def fetch_rss(cfg: dict) -> list[Item]:
    """RSS/Atom 범용 수집기. 블로그, 릴리스 피드, 커뮤니티 모두 이걸로 처리."""
    resp = _get(cfg["url"])
    feed = feedparser.parse(resp.content)
    include = [k.lower() for k in cfg.get("keywords", [])]
    exclude = [k.lower() for k in cfg.get("exclude_keywords", [])]
    items = []
    for e in feed.entries[: cfg.get("limit", 20)]:
        title = (e.get("title") or "").strip()
        link = e.get("link") or ""
        if not title or not link:
            continue
        haystack = f"{title} {e.get('summary', '')}".lower()
        if include and not any(k in haystack for k in include):
            continue
        if exclude and any(k in title.lower() for k in exclude):
            continue
        items.append(
            Item(
                id=f"{cfg['id']}:{e.get('id') or link}",
                title=title,
                url=link,
                source=cfg.get("name", cfg["id"]),
                category=cfg.get("category", "it"),
            )
        )
    return items


FETCHERS = {
    "kev": fetch_kev,
    "ghsa": fetch_ghsa,
    "nvd": fetch_nvd,
    "rss": fetch_rss,
}
