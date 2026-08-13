# Fraud Daily Brief — Hermes Agent Design

Date: 2026-08-13
Status: approved for spec review
Project: `~/Projects/hermes-fraud-intel`

Sibling of `~/Projects/hermes-finance-brief`. Same runtime, different beat, separate cron.

## Goal

A local Hermes Agent job that, every morning at **07:30 Asia/Singapore**, scans a curated set of fraud / AML / identity / payments RSS feeds, clusters new items, and writes a cited markdown brief covering:

1. Novel fraud and schemes
2. Vendor and service-provider M&A (plus funding that is clearly a control-vendor deal)
3. Product and tech developments
4. Enforcement and regulation

Plus an **Insights** section: through-lines from overlapping dump items (or one item plus yesterday), not per-item restatements.

No cloud LLM keys. Runs on Ollama. Reuses the 16GB-safe models already installed for the finance brief. Default model is 7B-64k; `--model 14b` is an opt-in on machines with ~24GB+.

## Non-goals

- Watching every publisher on the internet
- A named vendor watchlist or page-diff monitor (v1 is RSS clustering, not `competitor-news-monitor`)
- Wiring into Skuld / CTI command-center crawlers
- Telegram, email, or other delivery channels
- Fetching or copying paywalled full article text
- Trading, investment, or “buy this vendor” advice
- Parallel sub-agents (too heavy for 16GB)
- Firecrawl / Nous Portal / paid web search
- A second agent framework
- Sharing OPML, skill, or cron prompt with `finance-daily-brief`

## Architecture

```
07:30 SGT
  Hermes gateway (existing user launchd service)
    → fresh session + skill `fraud-daily-brief`
      → python3 scripts/dump_recent.py  (RSS/Atom title+description, last 24h, keyword filter on trade press)
      → local Ollama (qwen2.5 7B-64k default, 14B-64k optional — already created by finance-brief)
      → briefs/YYYY-MM-DD.md
      → ~/.hermes/cron/output/ (Hermes local delivery)
```

Hermes Agent is the runtime. This repo is the skill, OPML, dump script, check script, and install script. It does not reinstall Hermes or recreate Ollama tags if they already exist.

Finance stays at 07:00. Fraud is staggered to **07:30** so the two jobs do not contend for the same 7B+64k slot.

## Model (16GB profile)

Same contract as finance-brief. Hermes requires a 64,000-token context window. Tags `qwen2.5-7b-64k` and `qwen2.5-14b-64k` already exist on this Mac.

| Profile | Base | Why |
|---|---|---|
| Default (16GB-safe) | `qwen2.5-7b-64k` | Already installed; weights + KV + macOS + Hermes fit 16GB |
| Optional quality bump | `qwen2.5-14b-64k` | Use only if the machine has ~24GB+ and does not swap |

`scripts/install.sh` defaults to the 7B-64k tag (`--model 7b`). If the tag is missing, create it from the copied Modelfile; if it exists, skip `ollama create`. Provider: `http://localhost:11434/v1`. No API key. Unused Hermes toolsets (browser, image, TTS, vision) stay disabled.

LM Studio is not used.

## Components

| Path | Role |
|---|---|
| `feeds/fraud.opml` | Curated RSS/Atom list, grouped by the four brief sections |
| `skills/fraud-daily-brief/SKILL.md` | Cron procedure: dump → cluster → write |
| `skills/fraud-daily-brief/references/brief-template.md` | Output shape the model must follow |
| `skills/fraud-daily-brief/references/cron-prompt.txt` | Self-contained cron prompt (no chat memory) |
| `ollama/Modelfile.7b` / `Modelfile.14b` | Copies of finance-brief wrappers; install skips create if tags exist |
| `scripts/dump_recent.py` | Last-24h title+description dump; follows redirects; keyword filter on trade-press items; enforcement feeds pass through |
| `scripts/check_brief.py` | Offline/structural check |
| `scripts/install.sh` | Skill copy + OPML + cron; Hermes/Ollama only if missing |
| `briefs/` | Dated markdown (`YYYY-MM-DD.md`) |
| `~/.hermes/skills/fraud-daily-brief/` | Installed skill copy |

Copy `dump_recent.py` from finance-brief, then change: default OPML path, User-Agent, implication regexes, insight-seed terms, and the trade-press keyword allowlist. Do not import from the finance repo at runtime.

### Feed list (v1 OPML)

Headlines + RSS descriptions only. **Each `xmlUrl` appears in exactly one OPML group** (no duplicate fetches). OPML group decides the keyword-filter lane (trade vs enforcement), not the brief section — clustering assigns the section from the item text. **Probe each `xmlUrl` at implement** (HTTP GET, follow redirects, parse as RSS/Atom). If a URL 404s, times out, or is not a feed, keep it commented in the OPML with the date and the URL that failed — do not substitute a guessed URL. Skip paywalled full-text (FT, WSJ, Bloomberg).

Intended outlets (names are the contract; URLs are discovered, not invented). Each outlet is listed once:

**Novel fraud and schemes** (trade, keyword-filtered)

- Krebs on Security
- The Paypers (fraud/AML lane if a distinct feed exists)
- ACFE / Fraud Magazine if a public RSS exists

**Vendor M&A** (trade, keyword-filtered)

- Finextra
- Payments Dive
- Banking Dive

**Product and tech** (trade, keyword-filtered)

- Biometric Update
- PYMNTS (security/fraud lane if distinct; otherwise main feed)

**Enforcement and regulation** (no keyword filter)

- FTC press releases
- DOJ press releases
- CFPB newsroom
- FinCEN news
- FCA news
- ICO news
- SEC press releases (overlap with finance-brief is acceptable; fraud job still dumps them)

If an enforcement outlet has no public RSS after probing, comment it out. Do not scrape HTML as a substitute.

Add feeds later by editing the OPML. Point `xmlUrl` at a URL that returns 200 after redirects.

### Keyword allowlist (trade press only)

Enforcement/regulation OPML groups are **not** keyword-filtered (agency feeds are already the beat; over-filtering drops circulars that never say “fraud”).

Trade-press items (Novel fraud, Vendor M&A, Product/tech groups) are kept only if title or description matches at least one allowlist term (case-insensitive). v1 terms:

`fraud`, `scam`, `mule`, `launder`, `AML`, `BSA`, `KYC`, `KYB`, `CFT`, `sanctions`, `SAR`, `identity`, `synthetic`, `ATO`, `account takeover`, `APP fraud`, `authorized push`, `confirmation of payee`, `CoP`, `BEC`, `phishing`, `spoof`, `impersonat`, `chargeback`, `first-party`, `friendly fraud`, `refund abuse`, `bust-out`, `CNP`, `card-not-present`, `3DS`, `PSD2`, `biometric`, `passkey`, `liveness`, `deepfake`, `acquisition`, `acquires`, `acquired`, `merger`, `merges`, `buyout`, `take-private`, `Series A`, `Series B`, `Series C`, `raises`, `funding`

If a real on-beat story is dropped, add a term to this list. Do not fetch article HTML to recover it.

### Implication flags

Dump marks each kept item:

- `Implication: allowed` if the description contains a digit **or** a named official (`FTC`, `DOJ`, `CFPB`, `FinCEN`, `FCA`, `ICO`, `SEC`, `OCC`, `FDIC`, `CFTC`, `OFAC`, `FBI`, `Europol`) **or** a deal word (`acqui`, `merger`, `buyout`, `take-private`) already in the dump text.
- `Implication: omit` otherwise.

The agent may write an Implication line only when allowed, and then only restating a number, named agency action, or named deal already in the dump. No forecast, no “this will hurt Vendor X’s pipeline.”

## Data flow

1. Gateway fires cron `30 7 * * *` in timezone `Asia/Singapore`.
2. Fresh Hermes session loads `fraud-daily-brief`. Prompt is self-contained (cron has no chat memory).
3. Agent runs `terminal(command="python3 scripts/dump_recent.py", timeout=180)` from the repo workdir. That dump is the only source text.
4. Agent clusters dump items into the four sections from item text (not OPML group). Collapse duplicate headlines (same story, multiple outlets) into one item with multiple source URLs. Prefer 3–5 items per section when available; fewer is fine. Skip a dump item that matched only a deal-word (`merger`, `funding`, …) and has no fraud / AML / KYC / identity / payments-control angle — generic bank M&A is not Vendor M&A.
5. Agent writes `briefs/YYYY-MM-DD.md` using the template. Hermes local delivery also stores the same text under `~/.hermes/cron/output/`.
6. If the dump has no items: still write the dated brief with “nothing material” under each section. Do not invent stories.
7. Copy the dump’s “Sources that failed” into the footer. If `dump_recent.py` itself fails, write `briefs/YYYY-MM-DD.failed.md`.

### Brief format

- Title: `Fraud brief — YYYY-MM-DD`
- One-line disclaimer: local Hermes digest from RSS title + description. Not advice.
- Four sections, then `## Insights`
- Each item: headline, 1–2 sentence summary from the dump Description, optional Implication line, source URL(s)
- Insights: 3–5 bullets from the dump’s `## Insight seeds`. Each bullet cites ≥2 dump URLs from one seed, or one URL plus yesterday. Facts only.
- Footer: item count, failed sources, generated locally
- Summaries only. No verbatim article body. No browser fetch.

## Error handling

- Dead feed: dump continues. Failed sources go in a “Sources that failed” footer.
- Ollama down / model missing: cron writes `briefs/YYYY-MM-DD.failed.md`, not silence.
- Gateway not running: cron never fires. Health check is `hermes cron status`.
- Duplicate stories: one item, multiple links.
- Paywalls: RSS title + description only; do not fetch article HTML.
- Keyword miss: add an allowlist term; do not scrape.
- 14B swapping: use the 7B-64k tag.
- Empty day: “nothing material” brief, still dated, still written.
- Collision with finance: fraud is 07:30, not 07:00.

## Cron prompt (canonical)

Exact wording lives in `skills/fraud-daily-brief/references/cron-prompt.txt` and is passed to `hermes cron create`. Must include: run `dump_recent.py`; dump is the only source text; four sections; collapse duplicates; Implication only when flagged; Insights from seeds with dual cites; write `briefs/YYYY-MM-DD.md`; empty dump still writes; dump crash writes `.failed.md`; never invent; not advice.

## Testing

One runnable check, no test framework:

`scripts/check_brief.py`

- OPML is well-formed XML and contains at least one uncommented feed in each of the four categories
- Skill `SKILL.md` starts with YAML frontmatter, has `name: fraud-daily-brief`, and a non-empty body
- Template contains the four section headings plus `## Insights`
- Optional: HTTP GET each uncommented feed URL; skip with `--offline`

Live Ollama summarization is a manual step after install: `hermes cron run fraud-daily-brief` and confirm a file appears in `briefs/`.

`dump_recent.py` must run against the OPML without crashing when a single feed 404s.

## Install sequence

1. Create `~/Projects/hermes-fraud-intel` (done) and move the agent there (done).
2. `scripts/install.sh`:
   - if `hermes` is missing, install Hermes Agent (same path as finance-brief)
   - if `qwen2.5-7b-64k` (or 14b) is missing, `ollama create` from the Modelfile; otherwise skip
   - do not overwrite `~/.hermes/config.yaml` if the custom Ollama endpoint is already set
   - copy skill to `~/.hermes/skills/fraud-daily-brief/`
   - create cron `fraud-daily-brief` at `30 7 * * *` with `--workdir` this repo, `--skill fraud-daily-brief`, `--deliver local`
   - do not delete or edit the existing `finance-daily-brief` cron
3. Run `scripts/check_brief.py --offline`
4. Trigger once by hand and inspect the sample brief

## Success criteria

- `hermes cron list` shows `fraud-daily-brief` at 07:30 and still shows `finance-daily-brief` at 07:00
- `hermes cron status` shows the gateway running
- A weekday 07:30 SGT run produces `briefs/YYYY-MM-DD.md` with cited items or an explicit empty/failure brief
- `scripts/check_brief.py --offline` exits 0
- Trade-press dump items are on-beat (keyword allowlist); enforcement items are unfiltered
- Process RSS + Ollama stays within a 16GB-class budget on the 7B profile
