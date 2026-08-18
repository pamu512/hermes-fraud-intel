#!/usr/bin/env python3
"""Offline/structural check for hermes-fraud-intel assets. Stdlib only."""

from __future__ import annotations

import argparse
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPML = ROOT / "feeds" / "fraud.opml"
SKILL = ROOT / "skills" / "fraud-daily-brief" / "SKILL.md"
TEMPLATE = ROOT / "skills" / "fraud-daily-brief" / "references" / "brief-template.md"
CRON_PROMPT = ROOT / "skills" / "fraud-daily-brief" / "references" / "cron-prompt.txt"
DUMP = ROOT / "scripts" / "dump_recent.py"

RSS_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Fixture</title>
    <item>
      <title>FTC fines processor $5 million over fraud controls</title>
      <link>https://example.com/ftc</link>
      <pubDate>Wed, 12 Aug 2026 12:00:00 GMT</pubDate>
      <description>&lt;p&gt;The FTC fined the processor $5 million for weak fraud controls.&lt;/p&gt;</description>
    </item>
    <item>
      <title>A color story about branding</title>
      <link>https://example.com/brand</link>
      <pubDate>Wed, 12 Aug 2026 11:00:00 GMT</pubDate>
      <description>No digits, no officials, no deals.</description>
    </item>
    <item>
      <title>Old mule network bust</title>
      <link>https://example.com/old</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <description>Police dismantled a mule network.</description>
    </item>
  </channel>
</rss>
"""

ATOM_FIXTURE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom fixture</title>
  <entry>
    <title>Vendor X acquires Y</title>
    <link href="https://example.com/deal"/>
    <updated>2026-08-12T15:00:00Z</updated>
    <summary>Vendor X acquires Y in a take-private deal.</summary>
  </entry>
</feed>
"""

REQUIRED_CATEGORIES = (
    "Novel fraud and schemes",
    "Vendor M&A",
    "Product and tech",
    "Enforcement and regulation",
)
REQUIRED_TEMPLATE_HEADINGS = (
    "## Novel fraud and schemes",
    "## Vendor M&A",
    "## Product and tech",
    "## Enforcement and regulation",
    "## Insights",
)
REQUIRED_CRON_PHRASES = (
    "last 24",
    "four sections",
    "collapse duplicate",
    "never invent",
    "briefs/",
    "dump_recent.py",
    "--write-brief",
    "do not rewrite",
    "implication",
    "insight seeds",
    "## insights",
    "[title](url)",
    "failed.md",
)
REQUIRED_SKILL_PHRASES = (
    "dump_recent.py",
    "--write-brief",
    "Do not rewrite",
    "Implication: allowed",
    "Insight seeds",
    "## Insights",
    "[title](url)",
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "hermes-fraud-intel/1.0 (+https://github.com/pamu512/hermes-fraud-intel)"
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        fail(f"{SKILL.relative_to(ROOT)} must start with YAML frontmatter (---)")
    rest = text[3:]
    if rest.startswith("\r\n"):
        rest = rest[2:]
    elif rest.startswith("\n"):
        rest = rest[1:]
    else:
        fail(f"{SKILL.relative_to(ROOT)} frontmatter opener is malformed")
    end = rest.find("\n---")
    if end < 0:
        fail(f"{SKILL.relative_to(ROOT)} is missing closing ---")
    raw = rest[:end]
    body = rest[end + len("\n---") :].lstrip("\n")
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body


def collect_feeds(root: ET.Element) -> dict[str, list[tuple[str, str]]]:
    body = root.find("body")
    if body is None:
        fail("OPML has no <body>")
    feeds: dict[str, list[tuple[str, str]]] = {}
    for group in body.findall("outline"):
        category = (group.get("text") or "").strip()
        if not category:
            continue
        items: list[tuple[str, str]] = []
        for child in group.findall("outline"):
            xml_url = (child.get("xmlUrl") or "").strip()
            title = (child.get("text") or xml_url).strip()
            if xml_url:
                items.append((title, xml_url))
        feeds[category] = items
    return feeds


def check_opml() -> dict[str, list[tuple[str, str]]]:
    if not OPML.is_file():
        fail(f"missing {OPML.relative_to(ROOT)}")
    try:
        tree = ET.parse(OPML)
    except ET.ParseError as exc:
        fail(f"OPML is not well-formed XML: {exc}")
    feeds = collect_feeds(tree.getroot())
    seen_urls: set[str] = set()
    for category in REQUIRED_CATEGORIES:
        items = feeds.get(category, [])
        if not items:
            fail(f"OPML category {category!r} needs at least one xmlUrl")
        for _title, url in items:
            if url in seen_urls:
                fail(f"duplicate xmlUrl {url}")
            seen_urls.add(url)
        print(f"OK  OPML {category}: {len(items)} feed(s)")
    return feeds


def check_skill() -> None:
    if not SKILL.is_file():
        fail(f"missing {SKILL.relative_to(ROOT)}")
    text = SKILL.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if meta.get("name") != "fraud-daily-brief":
        fail(f"skill name must be fraud-daily-brief, got {meta.get('name')!r}")
    if not body.strip():
        fail("SKILL.md body is empty")
    missing = [p for p in REQUIRED_SKILL_PHRASES if p not in body]
    if missing:
        fail(f"SKILL.md missing required phrases: {missing}")
    desc = meta.get("description", "")
    if len(desc) > 60:
        fail(f"skill description is {len(desc)} chars (max 60)")
    print(f"OK  skill {meta['name']}")


def check_template() -> None:
    if not TEMPLATE.is_file():
        fail(f"missing {TEMPLATE.relative_to(ROOT)}")
    text = TEMPLATE.read_text(encoding="utf-8")
    for heading in REQUIRED_TEMPLATE_HEADINGS:
        if heading not in text:
            fail(f"template missing heading {heading!r}")
    if "Fraud brief —" not in text:
        fail("template missing title 'Fraud brief —'")
    if "[Headline](https://example.com/story)" not in text:
        fail("template must embed the source as a markdown link on the headline")
    if "Implication: allowed" not in text and "Implication: omit" not in text:
        fail("template must show gated Implication allowed/omit")
    print("OK  brief template")


def check_cron_prompt() -> None:
    if not CRON_PROMPT.is_file():
        fail(f"missing {CRON_PROMPT.relative_to(ROOT)}")
    text = CRON_PROMPT.read_text(encoding="utf-8").lower()
    missing = [p for p in REQUIRED_CRON_PHRASES if p not in text]
    if missing:
        fail(f"cron prompt missing required phrases: {missing}")
    print("OK  cron prompt")


def check_dump_parser() -> None:
    if not DUMP.is_file():
        fail(f"missing {DUMP.relative_to(ROOT)}")
    sys.path.insert(0, str(DUMP.parent))
    import dump_recent  # type: ignore  # noqa: E402

    from datetime import datetime, timezone

    rss_items = dump_recent.parse_feed(RSS_FIXTURE.encode(), source="Fixture")
    titles = [i.title for i in rss_items]
    if "FTC fines processor $5 million over fraud controls" not in titles:
        fail(f"RSS parser missed FTC item, got {titles}")
    ftc = next(i for i in rss_items if i.title.startswith("FTC"))
    if "<p>" in ftc.description or "<P>" in ftc.description:
        fail("RSS description still has HTML tags")
    if "FTC fined the processor" not in ftc.description:
        fail("RSS description lost the lede")
    if not dump_recent.may_write_implication(ftc.title, ftc.description):
        fail("FTC fine must allow implication")
    brand = next(i for i in rss_items if "branding" in i.title)
    if dump_recent.may_write_implication(brand.title, brand.description):
        fail("branding story must omit implication")

    cutoff = datetime(2026, 8, 11, tzinfo=timezone.utc)
    recent = dump_recent.filter_since(rss_items, cutoff)
    recent_titles = [i.title for i in recent]
    if "Old mule network bust" in recent_titles:
        fail("filter_since kept a 2024 item")
    if "FTC fines processor $5 million over fraud controls" not in recent_titles:
        fail("filter_since dropped the in-window FTC item")

    atom_items = dump_recent.parse_feed(ATOM_FIXTURE.encode(), source="Atom fixture")
    if len(atom_items) != 1 or "acquires" not in atom_items[0].title.lower():
        fail(f"Atom parser failed: {atom_items}")
    if not dump_recent.may_write_implication(atom_items[0].title, atom_items[0].description):
        fail("named deal must allow implication")
    print("OK  dump_recent parser")
    check_keyword_filter(dump_recent, ftc, brand)
    check_insight_seeds(dump_recent)


def check_keyword_filter(dump_recent, ftc, brand) -> None:
    if not dump_recent.keep_item(ftc):
        fail("fraud item must be kept")
    if dump_recent.keep_item(brand):
        fail("branding item must be dropped")
    print("OK  keyword filter")
    check_routing(dump_recent)


def check_routing(dump_recent) -> None:
    Item = dump_recent.Item
    from datetime import datetime, timezone

    goldman = Item(
        title="Goldman Sachs to buy ETF provider NEOS for up to $2.25B",
        url="https://example.com/goldman",
        description="The deal is the bank’s second multibillion-dollar ETF-related acquisition.",
        published=None,
        source="Banking Dive",
    )
    if dump_recent.keep_item(goldman):
        fail("generic bank/ETF acquisition must be dropped")

    yuno = Item(
        title="Yuno Raises $45 Million to Drive Global Payments Push",
        url="https://example.com/yuno",
        description="The payments orchestration firm raised a Series B.",
        published=None,
        source="PYMNTS",
    )
    if not dump_recent.keep_item(yuno):
        fail("payments-vendor funding must be kept")
    if dump_recent.assign_section(yuno) != "Vendor M&A":
        fail(f"Yuno must be Vendor M&A, got {dump_recent.assign_section(yuno)!r}")

    mule = Item(
        title="New mule network moves APP fraud proceeds",
        url="https://example.com/mule",
        description="Mules cashed out APP fraud proceeds.",
        published=None,
        source="Krebs",
    )
    if dump_recent.assign_section(mule) != "Novel fraud and schemes":
        fail(f"mule/APP must be Novel fraud, got {dump_recent.assign_section(mule)!r}")

    maverick = Item(
        title="Maverick Payments Integrates Findustry AI Agent to Automate Chargeback Rebuttals",
        url="https://example.com/maverick",
        description="Merchants can access Chargeback Agent to automate the dispute workflow.",
        published=None,
        source="PYMNTS",
    )
    if dump_recent.assign_section(maverick) != "Product and tech":
        fail(f"chargeback product must be Product and tech, got {dump_recent.assign_section(maverick)!r}")

    acquirer = Item(
        title="finby boosts Wero to support the future of pan-European payments",
        url="https://example.com/finby",
        description="finby, a European payment service provider and acquirer, announced Wero for merchants.",
        published=None,
        source="Finextra Payments",
    )
    if dump_recent.assign_section(acquirer) == "Vendor M&A":
        fail("PSP 'acquirer' must not count as M&A")

    ftc = Item(
        title="FTC fines processor $5 million over fraud controls",
        url="https://example.com/ftc",
        description="The FTC fined the processor $5 million.",
        published=None,
        source="FTC",
    )
    if dump_recent.assign_section(ftc) != "Enforcement and regulation":
        fail(f"FTC fine must be Enforcement, got {dump_recent.assign_section(ftc)!r}")

    tplus = Item(
        title="T+1 Settlement: are firms ready for 2027?",
        url="https://example.com/t1",
        description="FCA asks if firms are ready for T+1 settlement.",
        published=None,
        source="FCA news",
    )
    if dump_recent.keep_item(tplus):
        fail("off-beat FCA market-structure item must be dropped")

    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    cutoff = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    future = Item(
        title="Tackling fraud as an ecosystem",
        url="https://example.com/event",
        description="What regulatory changes are needed to fight fraud?",
        published=datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc),
        source="Finextra Payments",
    )
    if dump_recent.filter_since([future], cutoff, now=now):
        fail("future-dated items must be dropped")

    many = [
        Item(
            title=f"APP fraud wave {i}",
            url=f"https://example.com/f{i}",
            description="APP fraud losses rose.",
            published=None,
            source="Wire",
        )
        for i in range(8)
    ]
    routed = dump_recent.route_items(many)
    if len(routed["Novel fraud and schemes"]) > 5:
        fail("route_items must cap each section at 5")
    print("OK  routing")


def check_insight_seeds(dump_recent) -> None:
    Item = dump_recent.Item
    fraud_a = Item(
        title="New mule network moves APP fraud proceeds",
        url="https://example.com/mule",
        description="Mules cashed out APP fraud proceeds through mules.",
        published=None,
        source="Krebs",
    )
    fraud_b = Item(
        title="Banks freeze mule accounts after APP fraud spike",
        url="https://example.com/app",
        description="Lenders froze mule accounts tied to APP fraud.",
        published=None,
        source="Finextra",
    )
    other = Item(
        title="A color story about branding",
        url="https://example.com/brand",
        description="No digits, no officials, no deals.",
        published=None,
        source="Fixture",
    )
    clusters = dump_recent.cluster_items([fraud_a, fraud_b, other])
    paired = next(
        (
            c
            for c in clusters
            if {i.url for i in c.items} == {"https://example.com/mule", "https://example.com/app"}
        ),
        None,
    )
    if paired is None:
        fail(f"cluster_items must group the two mule stories, got {clusters}")
    the_items = [
        Item(
            title=f"The update {i}",
            url=f"https://example.com/t{i}",
            description="The market and the news today.",
            published=None,
            source="Wire",
        )
        for i in range(8)
    ]
    if any(c.term == "the" for c in dump_recent.cluster_items(the_items + [fraud_a, fraud_b])):
        fail("stopwords like 'the' must not become insight themes")

    yesterday = "# Fraud brief\n### New mule network moves APP fraud proceeds\n"
    new, continuing = dump_recent.vs_yesterday([fraud_a, fraud_b], yesterday)
    if [i.url for i in continuing] != ["https://example.com/mule"]:
        fail(f"vs_yesterday continuing={continuing}")
    if [i.url for i in new] != ["https://example.com/app"]:
        fail(f"vs_yesterday new={new}")

    seeds = dump_recent.format_insight_seeds(clusters, new, continuing, yesterday_name="2026-08-12.md")
    if "## Insight seeds" not in seeds:
        fail("format_insight_seeds missing heading")
    if "mule" not in seeds.lower() or "https://example.com/mule" not in seeds:
        fail("insight seeds must cite clustered URLs")
    print("OK  insight seeds")
    check_collapse(dump_recent)
    check_render(dump_recent)


def check_collapse(dump_recent) -> None:
    Item = dump_recent.Item
    finextra = Item(
        title="Yuno Raises $45 Million to Drive Global Payments Push - Finextra",
        url="https://www.finextra.com/newsarticle/yuno",
        description="The payments orchestration firm raised a Series B.",
        published=None,
        source="Finextra Fraud",
    )
    pymnts = Item(
        title="Yuno Raises $45 Million to Drive Global Payments Push",
        url="https://www.pymnts.com/yuno-raises-45-million/",
        description="Yuno raised $45 million in a Series B for payments orchestration.",
        published=None,
        source="PYMNTS",
    )
    collapsed = dump_recent.collapse_duplicates([finextra, pymnts])
    if len(collapsed) != 1:
        fail(f"same story must collapse to one item, got {len(collapsed)}")
    urls = {collapsed[0].url, *(url for _src, url in collapsed[0].extra)}
    if urls != {finextra.url, pymnts.url}:
        fail(f"collapsed item must keep both URLs, got {urls}")
    rendered = dump_recent.render_brief(
        dump_recent.route_items(collapsed), [], [], "2026-08-18", total=1
    )
    if finextra.url not in rendered or pymnts.url not in rendered:
        fail("render_brief must embed both outlet URLs")

    ohio = Item(
        title="Card ring busted in Ohio",
        url="https://example.com/ohio",
        description="Police arrested a card fraud ring in Ohio.",
        published=None,
        source="Krebs",
    )
    deep = Item(
        title="Deepfake KYC bypass hits lenders",
        url="https://example.com/kyc",
        description="Deepfake videos beat KYC liveness checks at banks.",
        published=None,
        source="Biometric Update",
    )
    clusters = dump_recent.cluster_items([ohio, deep])
    if any(c.term == "fraud" and {i.url for i in c.items} == {ohio.url, deep.url} for c in clusters):
        fail("unrelated stories must not cluster on generic fraud")
    print("OK  collapse + entity clusters")


def check_render(dump_recent) -> None:
    long = (
        "Social engineering doubled last year. "
        "Firms are buying ready-made scam kits. "
        "This third sentence must be dropped from the lede."
    )
    lede = dump_recent.clip_lede(long)
    if "third sentence" in lede:
        fail("clip_lede must stop after two sentences")
    if "doubled last year" not in lede:
        fail("clip_lede lost the first sentence")
    us = dump_recent.clip_lede(
        "The U.S. Treasury Department is expanding its campaign against federal student aid fraud."
    )
    if "Treasury" not in us:
        fail(f"clip_lede must not split on U.S.: {us!r}")

    Item = dump_recent.Item
    mule = Item(
        title="New mule network moves APP fraud proceeds",
        url="https://example.com/mule",
        description="Mules cashed out APP fraud proceeds through mule accounts.",
        published=None,
        source="Krebs",
    )
    ftc = Item(
        title="FTC fines processor $5 million over fraud controls",
        url="https://example.com/ftc",
        description="The FTC fined the processor $5 million for weak fraud controls.",
        published=None,
        source="FTC",
    )
    grouped = dump_recent.route_items([mule, ftc])
    clusters = dump_recent.cluster_items([mule, ftc])
    text = dump_recent.render_brief(grouped, clusters, [], "2026-08-13", total=2)
    for heading in (
        "## Novel fraud and schemes",
        "## Vendor M&A",
        "## Product and tech",
        "## Enforcement and regulation",
        "## Insights",
    ):
        if heading not in text:
            fail(f"render_brief missing {heading!r}")
    if "Fraud brief — 2026-08-13" not in text:
        fail("render_brief missing title")
    if "Generated locally" not in text:
        fail("render_brief missing footer")
    if f"]({mule.url})" not in text:
        fail("render_brief must embed markdown links on headlines")
    if dump_recent.verify_brief_urls(text, {mule.url, ftc.url}):
        fail("render_brief URLs must all be dump URLs")
    invented = dump_recent.verify_brief_urls(
        text + "\n[nope](https://evil.example/invented)\n",
        {mule.url, ftc.url},
    )
    if not invented:
        fail("verify_brief_urls must flag URLs that were not in the dump")
    dropped = dump_recent.verify_brief_urls("no links here", {mule.url, ftc.url})
    if not dropped:
        fail("verify_brief_urls must flag dropped dump URLs")
    if dump_recent.implication_fact(
        "Maverick Payments Integrates Findustry",
        "the companies said in a Thursday (Aug. 13) press release emailed to PYMNTS.",
    ):
        fail("a date in a press-release clause must not become an Implication")
    ftc_fact = dump_recent.implication_fact(
        "FTC fines processor $5 million over fraud controls",
        "The FTC fined the processor $5 million for weak fraud controls.",
    )
    if not ftc_fact or "FTC" not in ftc_fact:
        fail(f"FTC fine must yield an Implication, got {ftc_fact!r}")
    if dump_recent.sanitize_lede("See https://evil.example/x", "safe lede") != "safe lede":
        fail("sanitize_lede must drop invented URLs")
    if dump_recent.sanitize_lede("[nope](https://evil.example/x)", "safe lede") != "safe lede":
        fail("sanitize_lede must drop markdown links")
    if "Social engineering" not in dump_recent.sanitize_lede(
        "Social engineering is scaling against banks.",
        "fallback",
    ):
        fail("sanitize_lede must keep a clean rewrite")
    if not dump_recent.rewrite_skipped(text, text):
        fail("rewrite_skipped must catch an unchanged dump skeleton")
    if dump_recent.rewrite_skipped(text + "\nparaphrase\n", text):
        fail("rewrite_skipped must allow a paraphrased brief")
    if "[truncated]" in text:
        fail("render_brief must not write [truncated]")
    print("OK  render_brief")


def get_url(url: str, timeout: float = 20.0) -> tuple[bool, str]:
    last = "unknown error"
    for _ in range(2):
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                code = getattr(resp, "status", None) or resp.getcode()
                body = resp.read(200)
                if not (200 <= int(code) < 400):
                    last = f"GET HTTP {code}"
                    continue
                head = body.lstrip()[:80].lower()
                if b"<html" in head or head.startswith(b"<!doctype html"):
                    return False, "GET 200 but HTML, not RSS/Atom"
                return True, f"GET {code}"
        except Exception as exc:  # noqa: BLE001 — report any network failure
            last = str(exc)
    return False, last


def check_feed_heads(feeds: dict[str, list[tuple[str, str]]]) -> None:
    failures: list[str] = []
    for category, items in feeds.items():
        if category not in REQUIRED_CATEGORIES:
            continue
        for title, url in items:
            ok, detail = get_url(url)
            status = "OK " if ok else "FAIL"
            print(f"{status} GET {category} / {title}: {detail}")
            if not ok:
                failures.append(f"{title} ({url}): {detail}")
    if failures:
        fail("feed GET failed:\n  " + "\n  ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check fraud-brief repo assets")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Skip HTTP GET of feed URLs",
    )
    args = parser.parse_args()
    feeds = check_opml()
    check_skill()
    check_template()
    check_cron_prompt()
    check_dump_parser()
    if not args.offline:
        check_feed_heads(feeds)
    print("PASS")


if __name__ == "__main__":
    main()
