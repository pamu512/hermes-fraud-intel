#!/usr/bin/env python3
"""Dump last-N-hours RSS/Atom items with title + description. Stdlib only.

Follows HTTP redirects. Does not fetch article HTML.
Trade-press and enforcement items are beat-filtered; dump is pre-clustered and capped.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPML = ROOT / "feeds" / "fraud.opml"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "hermes-fraud-intel/1.0 (+https://github.com/pamu512/hermes-fraud-intel)"
)
DESC_MAX = 500
MAX_PER_SECTION = 5
SECTIONS = (
    "Novel fraud and schemes",
    "Vendor M&A",
    "Product and tech",
    "Enforcement and regulation",
)
OFFICIAL = re.compile(
    r"\b(FTC|DOJ|CFPB|FinCEN|FCA|ICO|SEC|OCC|FDIC|CFTC|OFAC|FBI|Europol)\b",
    re.I,
)
DEAL = re.compile(
    r"(?i)\b(acqui(?:re[ds]?|sition)|merger|merges|buyout|take-private|raises|funding|series\s+[abc])\b",
)
PRODUCT = re.compile(
    r"(?i)\b(biometric|passkey|liveness|deepfake|3ds|psd2|integrat\w*|"
    r"chargeback\s+agent|proofing)\b"
)
HAS_DIGIT = re.compile(r"\d")
MONEY = re.compile(r"(?i)\$[\d,.]+|\b\d[\d,]*\s*(million|billion|percent|%)\b")
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9$%-]{2,}")
HEADING = re.compile(r"^### (.+)$", re.M)
COP = re.compile(r"\bCoP\b")
BEAT = re.compile(
    r"(?i)\b("
    r"fraud|scam|mule|launder|aml|bsa|kyc|kyb|cft|sanctions|sar|identity|"
    r"synthetic|ato|account\s+takeover|app\s+fraud|authorized\s+push|"
    r"confirmation\s+of\s+payee|bec|phishing|spoof|impersonat\w*|"
    r"chargeback|first-party|friendly\s+fraud|refund\s+abuse|bust-out|"
    r"cnp|card-not-present|3ds|psd2|biometric|passkey|liveness|deepfake"
    r")\b"
)
VENDOR_HINT = re.compile(
    r"(?i)\b(payment|paytech|fintech|kyc|aml|fraud|identity|biometric|"
    r"chargeback|compliance|authenticat\w*|verif\w*|dispute|orchestration)\b"
)
STOP = frozenset(
    """
    a an the and or but not nor for nor as at by to of in on is it its it's
    are was were be been being have has had do does did will would could
    should may might must can this that these those then than them they
    their there here what when where which while who whom whose why how
    into onto over under from with without within across after before
    during against between among about also just only more most some such
    very much many both each other another own same too any all few
    news says said say telling tells told report reports reported
    market markets trading trade trader traders look looks looking
    day days week weeks month months year years today yesterday
    company companies firm firms after ahead still near since
    percent million billion trillion according via per
    """.split()
)
GENERIC_BEAT = frozenset(
    """
    fraud scam mule launder aml bsa kyc kyb cft sanctions sar identity
    synthetic ato phishing spoof chargeback cnp 3ds psd2 biometric
    passkey liveness deepfake bec
    """.split()
)
PHRASES = (
    "app fraud",
    "account takeover",
    "authorized push",
    "confirmation of payee",
    "friendly fraud",
    "refund abuse",
    "card-not-present",
    "first-party",
    "bust-out",
)
OUTLET_SUFFIX = re.compile(r"\s+[-–—|]\s+[A-Za-z][A-Za-z0-9 .&'/]{1,40}$")
BRIEFS_DIR = ROOT / "briefs"
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
MODEL_TAG = os.environ.get("FRAUD_BRIEF_MODEL", "qwen2.5-7b-64k")
MD_LINK = re.compile(r"\[.+\]\(.+\)")


@dataclass
class Item:
    title: str
    url: str
    description: str
    published: datetime | None
    source: str
    extra: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Cluster:
    term: str
    items: list[Item]


def local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def strip_html(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def may_write_implication(title: str, description: str) -> bool:
    blob = f"{title} {description}"
    return bool(HAS_DIGIT.search(blob) or OFFICIAL.search(blob) or DEAL.search(blob))


def _blob(item: Item) -> str:
    return f"{item.title} {item.description}"


def on_beat(item: Item) -> bool:
    blob = _blob(item)
    return bool(BEAT.search(blob) or COP.search(blob))


def is_control_deal(item: Item) -> bool:
    blob = _blob(item)
    return bool(DEAL.search(blob) and (on_beat(item) or VENDOR_HINT.search(blob)))


def keep_item(item: Item) -> bool:
    return on_beat(item) or is_control_deal(item)


def assign_section(item: Item) -> str | None:
    if not keep_item(item):
        return None
    blob = _blob(item)
    if is_control_deal(item):
        return "Vendor M&A"
    if OFFICIAL.search(blob):
        return "Enforcement and regulation"
    if PRODUCT.search(blob):
        return "Product and tech"
    return "Novel fraud and schemes"


def beat_score(item: Item) -> int:
    blob = _blob(item)
    return len(BEAT.findall(blob)) + (1 if COP.search(blob) else 0)


def route_items(items: list[Item]) -> dict[str, list[Item]]:
    grouped: dict[str, list[Item]] = {section: [] for section in SECTIONS}
    for item in items:
        section = assign_section(item)
        if section:
            grouped[section].append(item)
    for section, lst in grouped.items():
        lst.sort(
            key=lambda i: (
                -beat_score(i),
                -(i.published.timestamp() if i.published else 0),
            )
        )
        grouped[section] = lst[:MAX_PER_SECTION]  # ponytail: cap 5; raise MAX_PER_SECTION if 14B is the default
    return grouped


def parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    iso = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def child_text(el: ET.Element, names: set[str]) -> str:
    for child in list(el):
        if local_tag(child.tag) not in names:
            continue
        href = child.get("href")
        if href:
            return href.strip()
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def parse_feed(xml_bytes: bytes, source: str) -> list[Item]:
    root = ET.fromstring(xml_bytes)
    items: list[Item] = []
    for el in root.iter():
        kind = local_tag(el.tag)
        if kind not in {"item", "entry"}:
            continue
        title = strip_html(child_text(el, {"title"})) or "(untitled)"
        url = child_text(el, {"link", "id"})
        desc = child_text(el, {"description", "summary", "encoded"})
        published = parse_date(child_text(el, {"pubDate", "published", "updated", "date"}))
        items.append(
            Item(
                title=title,
                url=url,
                description=strip_html(desc)[:DESC_MAX],
                published=published,
                source=source,
            )
        )
    return items


def filter_since(
    items: list[Item],
    cutoff: datetime,
    now: datetime | None = None,
) -> list[Item]:
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    horizon = now + timedelta(hours=1)
    kept: list[Item] = []
    for item in items:
        if item.published is None:
            kept.append(item)
            continue
        if cutoff <= item.published <= horizon:
            kept.append(item)
    return kept


def item_tokens(item: Item) -> set[str]:
    blob = f"{item.title} {item.description}"
    raw = {t.lower() for t in TOKEN.findall(blob) if t.lower() not in STOP}
    entities = {
        t.lower()
        for t in re.findall(r"\b[A-Z][A-Za-z0-9$%-]{2,}\b", blob)
        if t.lower() not in STOP
    }
    low = blob.lower()
    phrases = {phrase.replace(" ", "-") for phrase in PHRASES if phrase in low}
    specific = entities | phrases | (raw - GENERIC_BEAT)
    if specific:
        return specific
    return {t for t in raw if BEAT.search(t) or t in GENERIC_BEAT}


def norm_title(title: str) -> str:
    text = (title or "").casefold().strip()
    text = OUTLET_SUFFIX.sub("", text)
    return re.sub(r"\s+", " ", text)


def norm_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/").lower()
    return f"{host}{path}"


def collapse_duplicates(items: list[Item]) -> list[Item]:
    groups: list[list[Item]] = []
    for item in items:
        title_key = norm_title(item.title)
        url_key = norm_url(item.url)
        placed = False
        for group in groups:
            if any(
                (title_key and norm_title(other.title) == title_key)
                or (url_key and norm_url(other.url) == url_key)
                for other in group
            ):
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    collapsed: list[Item] = []
    for group in groups:
        group.sort(
            key=lambda i: (
                -len(i.description or ""),
                -(i.published.timestamp() if i.published else 0),
            )
        )
        primary = group[0]
        seen = {primary.url}
        extras = list(primary.extra)
        for other in group[1:]:
            if other.url not in seen:
                extras.append((other.source, other.url))
                seen.add(other.url)
            for src, url in other.extra:
                if url not in seen:
                    extras.append((src, url))
                    seen.add(url)
        primary.extra = extras
        collapsed.append(primary)
    return collapsed


def cluster_items(items: list[Item], min_docs: int = 2) -> list[Cluster]:
    index: dict[str, list[int]] = defaultdict(list)
    for i, item in enumerate(items):
        for tok in item_tokens(item):
            index[tok].append(i)
    clusters: list[Cluster] = []
    seen: set[tuple[int, ...]] = set()
    for term, idxs in sorted(index.items(), key=lambda kv: (-len(set(kv[1])), kv[0])):
        uniq = tuple(sorted(set(idxs)))
        if len(uniq) < min_docs or len(uniq) > 12 or uniq in seen:
            continue
        seen.add(uniq)
        clusters.append(Cluster(term=term, items=[items[i] for i in uniq]))
        if len(clusters) >= 12:
            break
    return clusters


def vs_yesterday(items: list[Item], yesterday_text: str) -> tuple[list[Item], list[Item]]:
    old = {h.strip().lower() for h in HEADING.findall(yesterday_text or "")}
    new: list[Item] = []
    continuing: list[Item] = []
    for item in items:
        if item.title.strip().lower() in old:
            continuing.append(item)
        else:
            new.append(item)
    return new, continuing


def format_insight_seeds(
    clusters: list[Cluster],
    new: list[Item],
    continuing: list[Item],
    yesterday_name: str | None,
) -> str:
    lines = [
        "## Insight seeds",
        "",
        "Write ## Insights in the brief from these overlaps only. Each bullet must cite at least two dump URLs from one seed, or one dump URL plus yesterday. Do not add causes, forecasts, or advice that are not in the descriptions.",
        "",
    ]
    if not clusters:
        lines.append("No multi-item themes in this window. Write Insights: nothing to synthesize.")
        lines.append("")
    for cluster in clusters:
        lines.append(f"### Theme: {cluster.term} ({len(cluster.items)} items)")
        for item in cluster.items:
            lines.append(f"- {item.title} — {item.url}")
        lines.append("")
    if yesterday_name:
        lines.append(f"### Vs yesterday ({yesterday_name})")
        lines.append(f"- New headlines: {len(new)}")
        lines.append(f"- Continuing headlines: {len(continuing)}")
        for item in continuing[:8]:
            lines.append(f"- still present: {item.title} — {item.url}")
        lines.append("")
    else:
        lines.append("### Vs yesterday")
        lines.append("- No prior brief in briefs/. Synthesize from themes only.")
        lines.append("")
    return "\n".join(lines)


def latest_brief(briefs_dir: Path) -> Path | None:
    if not briefs_dir.is_dir():
        return None
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    files = sorted(
        p
        for p in briefs_dir.glob("*.md")
        if re.match(r"\d{4}-\d{2}-\d{2}$", p.stem) and p.stem != today
    )
    return files[-1] if files else None


def load_opml(path: Path) -> list[tuple[str, str, str]]:
    """Return (category, feed_title, xml_url)."""
    tree = ET.parse(path)
    body = tree.getroot().find("body")
    if body is None:
        raise ValueError(f"{path} has no <body>")
    out: list[tuple[str, str, str]] = []
    for group in body.findall("outline"):
        category = (group.get("text") or "").strip()
        for child in group.findall("outline"):
            xml_url = (child.get("xmlUrl") or "").strip()
            title = (child.get("text") or xml_url).strip()
            if xml_url:
                out.append((category, title, xml_url))
    return out


def fetch_xml(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def clip_lede(text: str, max_chars: int = 280) -> str:
    text = (text or "").strip()
    if not text:
        return "(no RSS description)"
    text = re.sub(r"\s*The post .* appeared first on .*$", "", text, flags=re.I).strip()
    protected = (
        text.replace("U.S.", "\x00US\x00")
        .replace("U.K.", "\x00UK\x00")
        .replace("D.C.", "\x00DC\x00")
        .replace("E.U.", "\x00EU\x00")
    )
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", protected) if s.strip()]
    if not sentences:
        sentences = [protected]
    out: list[str] = []
    for sentence in sentences[:2]:
        candidate = " ".join(out + [sentence]).strip() if out else sentence
        if out and len(candidate) > max_chars:
            break
        out.append(sentence)
    lede = " ".join(out).strip()
    if len(lede) > max_chars:
        lede = lede[: max_chars - 1].rsplit(" ", 1)[0].rstrip(".,;:") + "."
    return (
        lede.replace("\x00US\x00", "U.S.")
        .replace("\x00UK\x00", "U.K.")
        .replace("\x00DC\x00", "D.C.")
        .replace("\x00EU\x00", "E.U.")
    )


def implication_fact(title: str, description: str) -> str | None:
    blob = f"{title} {description}"
    if not (OFFICIAL.search(blob) or DEAL.search(blob) or MONEY.search(blob)):
        return None
    protected = (
        (description or "")
        .replace("U.S.", "\x00US\x00")
        .replace("U.K.", "\x00UK\x00")
        .replace("D.C.", "\x00DC\x00")
        .replace("E.U.", "\x00EU\x00")
        .replace("Aug.", "\x00Aug\x00")
        .replace("Mr.", "\x00Mr\x00")
    )
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", protected) if p.strip()]
    parts.append((title or "").strip())
    for sent in parts:
        sent = (
            sent.replace("\x00US\x00", "U.S.")
            .replace("\x00UK\x00", "U.K.")
            .replace("\x00DC\x00", "D.C.")
            .replace("\x00EU\x00", "E.U.")
            .replace("\x00Aug\x00", "Aug.")
            .replace("\x00Mr\x00", "Mr.")
        )
        if len(sent) < 40 or not sent[0].isupper():
            continue
        if not (OFFICIAL.search(sent) or DEAL.search(sent) or MONEY.search(sent)):
            continue
        if not sent.endswith((".", "!", "?")):
            continue
        return sent
    return None


def format_item(item: Item) -> str:
    allowed = "allowed" if may_write_implication(item.title, item.description) else "omit"
    also = ""
    if item.extra:
        also = "Also " + ", ".join(f"[{src}]({url})" for src, url in item.extra) + ".\n\n"
    return (
        f"### [{item.title}]({item.url})\n\n"
        f"{clip_lede(item.description)}\n\n"
        f"{also}"
        f"Implication: {allowed}\n"
    )


def sanitize_lede(text: str, fallback: str) -> str:
    text = (text or "").strip().strip("`").strip('"').strip("'")
    if not text:
        return fallback
    if hrefs(text) or "example.com" in text.lower() or "[truncated]" in text.lower() or MD_LINK.search(text):
        return fallback
    return clip_lede(text)


def ollama_reachable() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=5).read(256)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def ollama_generate(prompt: str, num_predict: int = 128) -> str:
    payload = json.dumps(
        {
            "model": MODEL_TAG,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2, "num_predict": num_predict, "num_ctx": 2048},
        }
    ).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode())
    return (data.get("response") or "").strip()


def ollama_lede(title: str, lede: str) -> str:
    prompt = (
        "Rewrite the RSS lede in 1-2 factual sentences. "
        "Do not add facts, names, numbers, URLs, or advice that are not in the lede. "
        "Return only the rewrite, no quotes, no markdown.\n\n"
        f"Headline: {title}\nLede: {lede}\n"
    )
    try:
        return sanitize_lede(ollama_generate(prompt), lede)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return lede


def ollama_insight(term: str, titles: list[str]) -> str:
    prompt = (
        f"One factual sentence about how these headlines relate to '{term}'. "
        "No URLs, no forecast, no advice. Return only the sentence.\n\n"
        + "\n".join(f"- {t}" for t in titles)
    )
    try:
        return sanitize_lede(ollama_generate(prompt, num_predict=80), "")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return ""


def polish_ledes(grouped: dict[str, list[Item]]) -> dict[str, str]:
    polished: dict[str, str] = {}
    for items in grouped.values():
        for item in items:
            fallback = clip_lede(item.description)
            polished[item.url] = ollama_lede(item.title, fallback)
    return polished


def render_insights(clusters: list[Cluster], polish: bool = False) -> list[str]:
    bullets: list[str] = []
    for cluster in clusters[:5]:
        if len(cluster.items) < 2:
            continue
        linked = "; ".join(f"[{i.title}]({i.url})" for i in cluster.items[:3])
        sentence = ollama_insight(cluster.term, [i.title for i in cluster.items[:3]]) if polish else ""
        if sentence:
            bullets.append(f"- {sentence} {linked}.")
        else:
            bullets.append(f"- {cluster.term}: {linked}.")
    return bullets or ["- nothing to synthesize"]


def render_brief(
    grouped: dict[str, list[Item]],
    clusters: list[Cluster],
    failures: list[str],
    day: str,
    total: int,
    polished: dict[str, str] | None = None,
    polish_insights: bool = False,
) -> str:
    lines = [
        f"# Fraud brief — {day}",
        "",
        "Local Hermes digest from RSS title + description. Not advice.",
        "",
    ]
    for section in SECTIONS:
        lines.append(f"## {section}")
        lines.append("")
        items = grouped.get(section, [])
        if not items:
            lines.append("nothing material")
            lines.append("")
            continue
        for item in items:
            lede = (polished or {}).get(item.url) or clip_lede(item.description)
            lines.append(f"### [{item.title}]({item.url})")
            lines.append("")
            lines.append(lede)
            lines.append("")
            if item.extra:
                also = ", ".join(f"[{src}]({url})" for src, url in item.extra)
                lines.append(f"Also {also}.")
                lines.append("")
            fact = implication_fact(item.title, item.description)
            if fact:
                lines.append(f"Implication: {fact}")
                lines.append("")
    lines.append("## Insights")
    lines.append("")
    lines.extend(render_insights(clusters, polish=polish_insights))
    lines.append("")
    lines.append("---")
    lines.append("")
    failed = "; ".join(failures) if failures else "none"
    lines.append(f"Items: {total} · Sources that failed: {failed} · Generated locally")
    lines.append("")
    return "\n".join(lines)


def dump_feeds(
    opml: Path, hours: int
) -> tuple[str, list[str], int, dict[str, list[Item]], list[Cluster]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    now = datetime.now(timezone.utc)
    failures: list[str] = []
    collected: list[Item] = []

    def pull(title: str, url: str) -> tuple[str, list[Item], str | None]:
        try:
            xml_bytes = fetch_xml(url)
            items = filter_since(parse_feed(xml_bytes, source=title), cutoff, now=now)
            return title, [item for item in items if keep_item(item)], None
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, TimeoutError, OSError, ValueError) as exc:
            return title, [], f"{title}: {exc}"

    feeds = list(load_opml(opml))
    workers = min(8, max(1, len(feeds)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(pull, title, url) for _category, title, url in feeds]
        for fut in as_completed(futures):
            _title, items, err = fut.result()
            if err:
                failures.append(err)
            collected.extend(items)

    collected = collapse_duplicates(collected)
    grouped = route_items(collected)
    chunks: list[str] = [
        f"# RSS dump — last {hours}h (UTC cutoff {cutoff.isoformat()})",
        "Already clustered and capped (max 5 per section). Keep every ### [title](url) line.",
        "Rewrite each lede into 1–2 sentences. Embed source links as markdown [text](url), not a Sources: line.",
        "Write Implication only when Implication: allowed. Never invent. Never write [truncated].",
        "",
    ]
    total = 0
    for section in SECTIONS:
        items = grouped[section]
        chunks.append(f"## {section}")
        chunks.append("")
        if not items:
            chunks.append("nothing material")
            chunks.append("")
            continue
        for item in items:
            chunks.append(format_item(item))
            total += 1

    yesterday_path = latest_brief(BRIEFS_DIR)
    yesterday_text = yesterday_path.read_text(encoding="utf-8") if yesterday_path else ""
    new, continuing = vs_yesterday(collected, yesterday_text)
    routed_flat = [item for items in grouped.values() for item in items]
    clusters = cluster_items(routed_flat)
    chunks.append(
        format_insight_seeds(
            clusters,
            new,
            continuing,
            yesterday_path.name if yesterday_path else None,
        )
    )
    if failures:
        chunks.append("## Sources that failed")
        chunks.append("")
        for line in failures:
            chunks.append(f"- {line}")
        chunks.append("")
    chunks.append(f"Items: {total} · Failed sources: {len(failures)}")
    return "\n".join(chunks).rstrip() + "\n", failures, total, grouped, clusters


URL_HREF = re.compile(r"https?://[^\s\]>)]+")


def hrefs(text: str) -> set[str]:
    return set(URL_HREF.findall(text or ""))


def verify_brief_urls(brief: str, allowed: set[str]) -> list[str]:
    found = hrefs(brief)
    problems: list[str] = []
    extra = sorted(url for url in found if url not in allowed)
    missing = sorted(url for url in allowed if url not in found)
    if extra:
        problems.append("unknown URLs: " + ", ".join(extra))
    if missing:
        problems.append("dropped dump URLs: " + ", ".join(missing))
    return problems


def rewrite_skipped(brief: str, raw: str) -> bool:
    return (brief or "").strip() == (raw or "").strip()


def write_brief(opml: Path, hours: int, llm: bool = True) -> Path:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    try:
        _dump, failures, total, grouped, clusters = dump_feeds(opml, hours)
    except Exception as exc:
        path = BRIEFS_DIR / f"{day}.failed.md"
        path.write_text(f"# Fraud brief — {day}\n\nFailed: {exc}\n", encoding="utf-8")
        raise
    if llm and not ollama_reachable():
        path = BRIEFS_DIR / f"{day}.failed.md"
        path.write_text(
            f"# Fraud brief — {day}\n\nFailed: Ollama not reachable at {OLLAMA_URL}\n",
            encoding="utf-8",
        )
        print(f"dump_recent: Ollama not reachable at {OLLAMA_URL}", file=sys.stderr)
        raise SystemExit(1)
    raw_text = render_brief(grouped, clusters, failures, day, total)
    polished = polish_ledes(grouped) if llm else None
    text = render_brief(
        grouped,
        clusters,
        failures,
        day,
        total,
        polished=polished,
        polish_insights=llm,
    )
    raw = BRIEFS_DIR / f"{day}.raw.md"
    path = BRIEFS_DIR / f"{day}.md"
    raw.write_text(raw_text, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    problems = verify_brief_urls(text, hrefs(raw_text))
    if problems:
        path.write_text(raw_text, encoding="utf-8")
        print("FAIL: LLM rewrite invented or dropped URLs; restored dump brief", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        raise SystemExit(1)
    return path


def verify_today() -> None:
    day = datetime.now().astimezone().strftime("%Y-%m-%d")
    raw = BRIEFS_DIR / f"{day}.raw.md"
    path = BRIEFS_DIR / f"{day}.md"
    if not raw.is_file() or not path.is_file():
        print(f"dump_recent: missing {raw.name} or {path.name}", file=sys.stderr)
        raise SystemExit(1)
    problems = verify_brief_urls(
        path.read_text(encoding="utf-8"),
        hrefs(raw.read_text(encoding="utf-8")),
    )
    if not problems:
        print(f"OK  rewrite kept dump URLs ({path})")
        return
    path.write_text(raw.read_text(encoding="utf-8"), encoding="utf-8")
    print("FAIL: rewrite invented or dropped URLs; restored dump brief", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dump recent RSS title+description for the fraud brief")
    parser.add_argument("--opml", type=Path, default=DEFAULT_OPML)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument(
        "--write-brief",
        action="store_true",
        help="Write briefs/YYYY-MM-DD.md (Ollama ledes + dump URLs) and .raw.md fallback",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip Ollama; write dump skeleton only",
    )
    parser.add_argument(
        "--verify-brief",
        action="store_true",
        help="Require briefs/YYYY-MM-DD.md URLs match dump raw; restore raw on failure",
    )
    args = parser.parse_args()
    if not args.opml.is_file():
        print(f"dump_recent: missing {args.opml}", file=sys.stderr)
        raise SystemExit(1)
    if args.hours <= 0:
        print("dump_recent: --hours must be > 0", file=sys.stderr)
        raise SystemExit(1)
    if args.verify_brief:
        verify_today()
        return
    if args.write_brief:
        path = write_brief(args.opml, args.hours, llm=not args.no_llm)
        print(f"Wrote {path} (llm={'yes' if not args.no_llm else 'no'})")
        return
    text, _failures, _total, _grouped, _clusters = dump_feeds(args.opml, args.hours)
    sys.stdout.write(text)


if __name__ == "__main__":
    main()
