"""소스별 수집기. 각 함수는 설정 dict를 받아 Item 리스트를 돌려준다."""
from __future__ import annotations

import datetime as dt
import html
import os
import re
import time
from dataclasses import dataclass

import feedparser
import requests

UA = "news-bot/1.0"
TIMEOUT = 15


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
    body: str = ""  # 요약 재료 (릴리스 노트, CVE 설명, 글 요약 등)
    summary: str = ""  # Claude가 만든 한국어 요약
    label: str = ""  # Claude가 정한 라벨 (보안/IT/AI/AI-스킬), 없으면 category 사용
    importance: int | None = None  # Claude가 매긴 중요도 0~10 (None=평가 못 함)


def _get(url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": UA, **kwargs.pop("headers", {})}
    resp = requests.get(url, headers=headers, timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


_TAG = re.compile(r"<[^>]+>")


def _clean(text: str, limit: int = 1500) -> str:
    """HTML 태그를 걷어내고 공백을 정리해 요약 재료로 쓸 수 있게 자른다."""
    text = html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _gh_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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
                body=_clean(f"{v.get('shortDescription', '')} {v.get('requiredAction', '')}"),
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
                body=_clean(a.get("description") or a.get("summary") or ""),
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
                body=_clean(desc),
            )
        )
    return items


def fetch_rss(cfg: dict) -> list[Item]:
    """RSS/Atom 범용 수집기. 블로그, 릴리스 피드, 커뮤니티 모두 이걸로 처리."""
    resp = _get(cfg["url"])
    feed = feedparser.parse(resp.content)
    include = [k.lower() for k in cfg.get("keywords", [])]
    exclude = [k.lower() for k in cfg.get("exclude_keywords", [])]
    exclude_re = [re.compile(p, re.IGNORECASE) for p in cfg.get("exclude_regex", [])]
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
        if exclude_re and any(r.search(title) for r in exclude_re):
            continue
        raw = e.get("summary") or (e.get("content") or [{}])[0].get("value", "")
        prefix = cfg.get("title_prefix")
        items.append(
            Item(
                id=f"{cfg['id']}:{e.get('id') or link}",
                # 제목에 이미 이름이 있으면(예: "PHP 8.5.12") 접두어를 또 붙이지 않는다
                title=f"{prefix} {title}"
                if prefix and not title.lower().startswith(prefix.lower())
                else title,
                url=link,
                source=cfg.get("name", cfg["id"]),
                category=cfg.get("category", "it"),
                body=_clean(raw),
            )
        )
    return items


def fetch_github_search(cfg: dict) -> list[Item]:
    """GitHub 저장소 검색으로 새 에이전트 스킬·MCP 서버 등을 탐색 (별 개수 하한으로 스팸 차단)."""
    since_days = int(cfg.get("since_days", 7))
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)).strftime("%Y-%m-%d")
    min_stars = int(cfg.get("min_stars", 0))
    found: dict[str, Item] = {}
    for n, q in enumerate(cfg["queries"]):
        if n:
            time.sleep(2)  # 검색 API 분당 한도 보호
        query = q.format(since=since) + (f" stars:>={min_stars}" if min_stars else "")
        data = _get(
            "https://api.github.com/search/repositories",
            headers=_gh_headers(),
            params={
                "q": query,
                "sort": "stars",
                "order": "desc",
                "per_page": cfg.get("limit", 15),
            },
        ).json()
        for repo in data.get("items", []):
            name = repo["full_name"]
            if name in found:
                continue
            desc = repo.get("description") or ""
            topics = ", ".join((repo.get("topics") or [])[:6])
            found[name] = Item(
                id=f"gh:{name}",
                title=f"{name} — {desc}" if desc else name,
                url=repo["html_url"],
                source=f"GitHub ★{repo.get('stargazers_count', 0)}",
                category=cfg.get("category", "ai"),
                body=_clean(f"{desc} 토픽: {topics}"),
            )
    return list(found.values())


def fetch_hn_search(cfg: dict) -> list[Item]:
    """Hacker News 키워드 검색(Algolia). 최근 N시간 + 점수 하한."""
    since_ts = int(time.time()) - int(cfg.get("lookback_hours", 48)) * 3600
    min_points = int(cfg.get("min_points", 50))
    found: dict[str, Item] = {}
    for n, q in enumerate(cfg["queries"]):
        if n:
            time.sleep(0.5)
        data = _get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": q,
                "tags": "story",
                "numericFilters": f"points>{min_points},created_at_i>{since_ts}",
                "hitsPerPage": cfg.get("limit", 20),
            },
        ).json()
        for h in data.get("hits", []):
            oid, title = h.get("objectID"), h.get("title")
            if not oid or not title or oid in found:
                continue
            found[oid] = Item(
                id=f"hns:{oid}",
                title=title,
                url=h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                source=f"Hacker News {h.get('points', 0)}점",
                category=cfg.get("category", "ai"),
                body=_clean(h.get("story_text") or ""),
            )
    return list(found.values())


def fetch_hf_trending(cfg: dict) -> list[Item]:
    """Hugging Face 트렌딩 모델 (좋아요 하한으로 새로 뜨는 모델만)."""
    data = _get(
        "https://huggingface.co/api/models",
        params={"sort": "trendingScore", "limit": cfg.get("limit", 20)},
    ).json()
    min_likes = int(cfg.get("min_likes", 100))
    items = []
    for m in data:
        mid = m.get("id") or m.get("modelId")
        likes = m.get("likes", 0)
        if not mid or likes < min_likes:
            continue
        pipe = m.get("pipeline_tag") or ""
        tags = ", ".join((m.get("tags") or [])[:8])
        items.append(
            Item(
                id=f"hfm:{mid}",
                title=f"HF 트렌딩 모델: {mid}" + (f" ({pipe})" if pipe else ""),
                url=f"https://huggingface.co/{mid}",
                source=f"Hugging Face ♥{likes}",
                category=cfg.get("category", "ai"),
                body=_clean(f"task: {pipe}. tags: {tags}. downloads: {m.get('downloads', 0)}"),
            )
        )
    return items


def fetch_hf_papers(cfg: dict) -> list[Item]:
    """Hugging Face Daily Papers (추천 수 하한으로 화제가 된 논문만)."""
    data = _get(
        "https://huggingface.co/api/daily_papers", params={"limit": cfg.get("limit", 30)}
    ).json()
    min_up = int(cfg.get("min_upvotes", 20))
    items = []
    for entry in data:
        p = entry.get("paper") or {}
        pid = p.get("id")
        title = p.get("title") or entry.get("title")
        if not pid or not title or p.get("upvotes", 0) < min_up:
            continue
        items.append(
            Item(
                id=f"hfp:{pid}",
                title=title,
                url=f"https://huggingface.co/papers/{pid}",
                source=f"HF Papers ▲{p.get('upvotes', 0)}",
                category=cfg.get("category", "ai"),
                body=_clean(p.get("summary") or entry.get("summary") or ""),
            )
        )
    return items


FETCHERS = {
    "kev": fetch_kev,
    "ghsa": fetch_ghsa,
    "nvd": fetch_nvd,
    "rss": fetch_rss,
    "github_search": fetch_github_search,
    "hn_search": fetch_hn_search,
    "hf_trending": fetch_hf_trending,
    "hf_papers": fetch_hf_papers,
}
