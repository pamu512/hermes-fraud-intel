# hermes-fraud-intel

Local [Hermes Agent](https://github.com/nousresearch/hermes-agent) job that writes a cited fraud-intel brief every morning at **07:30** (machine timezone; use Asia/Singapore). Sibling of [`hermes-finance-brief`](https://github.com/pamu512/hermes-finance-brief) (07:00).

Covers **novel fraud and schemes**, **vendor/SP M&A**, **product and tech**, and **enforcement**. `scripts/dump_recent.py --write-brief` fetches RSS title + description, beat-filters, clusters, then rewrites each lede on local Ollama with clickable `[title](url)` headlines and overlapping-story **Insights**. Hermes cron only runs that script — it does not rewrite the brief. Implication lines only when the item has a dollar amount, named agency action, or named deal. Not advice.

## Install

Needs Ollama running (`ollama serve` or the Ollama app). Reuses Hermes + `qwen2.5-*-64k` if already installed by the finance brief.

```bash
./scripts/install.sh          # qwen2.5-7b-64k (16GB-safe); skips create if the tag exists
./scripts/install.sh --model 14b   # optional, ~24GB+ unified memory
```

That copies the skill, creates the 07:30 cron job, and leaves the finance cron untouched.

```bash
python3 scripts/check_brief.py --offline   # structural check
python3 scripts/dump_recent.py --write-brief   # write briefs/YYYY-MM-DD.md
hermes cron list
hermes cron status
hermes cron run fraud-daily-brief          # one-shot test
```

## Layout

| Path | Role |
|---|---|
| `feeds/fraud.opml` | Curated feeds |
| `skills/fraud-daily-brief/` | Cron procedure, template, prompt |
| `ollama/Modelfile.7b` / `.14b` | `num_ctx 65536` wrappers |
| `scripts/install.sh` | End-to-end setup |
| `scripts/dump_recent.py` | Last-24h RSS → cited `briefs/YYYY-MM-DD.md` |
| `scripts/check_brief.py` | Offline/structural check |
| `briefs/` | Dated markdown output (`YYYY-MM-DD.md`) |

Add feeds by editing the OPML. Point `xmlUrl` at a URL that returns 200 after redirects and is RSS/Atom, not HTML. Do not guess replacements for dead feeds.

## Health

If nothing appears at 07:30: `hermes cron status` (gateway must be running) and `curl http://localhost:11434/api/tags` (Ollama). Failures write `briefs/YYYY-MM-DD.failed.md` instead of staying silent.

Spec: [`docs/superpowers/specs/2026-08-13-fraud-daily-brief-design.md`](docs/superpowers/specs/2026-08-13-fraud-daily-brief-design.md).
