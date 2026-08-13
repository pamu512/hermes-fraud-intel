---
name: fraud-daily-brief
description: Cluster fraud RSS into a cited daily markdown brief.
version: 0.5.0
author: pamu (pamu512), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Fraud, RSS, Briefing, Cron]
    related_skills: []
---

# Fraud Daily Brief

Write today's cited markdown brief by running the dump script. It rewrites ledes on local Ollama and embeds `[title](url)` links. Do not fetch article HTML. Do not invent. Do not rewrite the brief. Not advice.

## When to Use

- Daily cron named `fraud-daily-brief`
- User asks for this morning's fraud / vendor-M&A / enforcement brief
- Don't use for: named vendor page-diffs, IOC hunting, or scraping FT/WSJ/Bloomberg full text

## Prerequisites

- `python3 scripts/dump_recent.py --write-brief` from this repo (already clustered, capped, beat-filtered, LLM ledes)
- Ollama serving `qwen2.5-7b-64k`
- Write access to this repo's `briefs/` directory (cron `--workdir`)

## How to Run

Use `terminal` only. Do not `write_file` the brief. Do not `cat`/`sed`.

```text
terminal(command="python3 scripts/dump_recent.py --write-brief", timeout=600)
```

## Quick Reference

- Dump+LLM: `terminal(command="python3 scripts/dump_recent.py --write-brief", timeout=600)`
- Output: `briefs/YYYY-MM-DD.md` with `[title](url)` headlines

## Procedure

1. Run `terminal(command="python3 scripts/dump_recent.py --write-brief", timeout=600)`. Completion: stdout is one line `Wrote briefs/YYYY-MM-DD.md (...)`. A dead feed must not abort the job.
2. Do not rewrite the file. Do not paraphrase items. Do not use `write_file` on `briefs/*.md`.
3. Your final response is that one-line status. Stop.
4. If `dump_recent.py` cannot run, write `briefs/YYYY-MM-DD.failed.md`. Do not fail silently.

## Pitfalls

- Cron sessions have no chat memory.
- The script already clusters into four sections, rewrites ledes, writes Implication: allowed facts only, and embeds markdown article links in items and ## Insights from Insight seeds.
- Never write a shell `cat` or `$(…)` into the brief.
- This is not advice.

## Verification

- `briefs/YYYY-MM-DD.md` exists (or `.failed.md` on hard failure)
- Title is `Fraud brief — YYYY-MM-DD`
- Four section headings match the template
- Every item headline is `### [text](http…)` or the section says nothing material
- `## Insights` bullets embed ≥2 markdown links (or say nothing to synthesize)
- File does not contain `[truncated]`
