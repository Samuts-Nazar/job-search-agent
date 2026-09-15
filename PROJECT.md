# PROJECT.md — Job Search Agent

Specification for a local, CLI-driven job search agent. Read this file fully before writing code. `CLAUDE.md` in this directory is a separate file and must not be modified.

## 1. Goal

A Python tool that, on each manual run, collects job postings from several sources, removes duplicates, filters and scores them against the candidate's CVs with a cheap LLM, prepares application packages (tailored CV PDF, cover letter, LinkedIn outreach draft, form-fill data) with a stronger LLM, and routes every decision through a Telegram bot for human approval.

The repository is public (portfolio). No personal data may ever be committed.

## 2. Run model

- Entry point: a CLI command `job` (e.g. Typer). Subcommands: `job` (full run), `job eval` (model evaluation), `job stats`, `job render-test` (template check).
- `job` runs the pipeline, then keeps running to handle Telegram interactions until the user stops the process (Ctrl+C). There is no daemon, no systemd service, no scheduler.
- On start, process pending Telegram updates first. Telegram keeps undelivered updates for at most 24 hours; older button presses are lost. All state lives in SQLite, and the bot command `/pending` re-sends every package still awaiting a decision.
- Graceful shutdown: finish the current step, persist state, exit.

## 3. Repository layout and privacy

```
job-agent/
  PROJECT.md
  config.example.yaml
  data.example/
    facts.example.yaml
    answers.example.yaml
  src/job_agent/
    cli.py
    config.py
    db.py
    sources/        # one module per source
    dedup.py
    prefilter.py
    llm/            # OpenRouter client, schemas, prompts
    scoring.py
    cv/             # Typst template, render, ATS checks
    letters.py
    outreach.py
    forms/          # ATS form fillers
    telegram_bot.py
    eval/
  tests/
```

`.gitignore` must include: `.env`, `config.yaml`, `data/`, `output/`, `*.db`, rendered PDFs, eval outputs.

Private runtime data (never committed), under `data/`:
- `cvs/` — source content for the four CV tracks (QA Automation, Manual QA, ML/AI, General). Added by the user later; until then use the example files.
- `facts.yaml` — the fact registry: every claim, project, metric, tool, date and link the candidate can truthfully state. Single source of truth for all generated text.
- `answers.yaml` — the answer bank for application forms (salary expectation, notice period, relocation, work authorization, time zone, languages with CEFR levels, links, etc.).
- `jobs.db` — SQLite.

Secrets in `.env`: `OPENROUTER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

## 4. Configuration (`config.yaml`)

- `mode`: `wide` or `selective`.
  - `wide` (default now): low score threshold, all target categories, no country filter, postings requiring a language the candidate lacks are still delivered with a flag.
  - `selective`: higher threshold, optional salary floor, stricter seniority filter.
- `thresholds` per mode (defaults are placeholders to tune after the first runs).
- `models`:
  - `bulk` — scoring (cheap, high volume).
  - `quality` — CV edits, cover letters, outreach drafts, free-text form answers.
  - `fallbacks` — ordered list per tier.
  - `eval_candidates` — list used by `job eval`.
- `sources` — enable/disable and per-source parameters.
- `categories` — Djinni: QA, QA Automation, Support, Sysadmin, ML AI, Python (editable).
- `budget` — monthly soft cap in USD (target 5–10); warn in Telegram when approaching it.

Model IDs must be switchable by editing config only. No model name is hardcoded outside defaults.

## 5. Pipeline

Build order is Phase 1 → Phase 2 → Phase 3 (section 11). Only steps marked LLM call a model.

### 5.1 Collect
Each source is a plugin returning a normalized `Posting` (source, url, title, company, location, remote type, description, published_at, salary if present, raw tags).

| Source | Method | Notes |
|---|---|---|
| Djinni | RSS `https://djinni.co/jobs/rss/` with category/keyword params | Verified: returns full descriptions and category tags. |
| DOU | RSS `https://jobs.dou.ua/vacancies/feeds/` (category / remote params) | Not yet verified — verify format and params when implementing. |
| Remote OK | JSON `https://remoteok.com/api` | Not yet verified. Keep required attribution link. |
| We Work Remotely | Category RSS feeds | Not yet verified. |
| LinkedIn Jobs | `python-jobspy` guest endpoints, low volume (~25–50 per run), randomized delays | Never log in, never use the user's account. |
| NoFluffJobs | No public API | Disabled by default; implement last, only if a stable method exists. |

Excluded: Work.ua, Robota.ua (anti-bot cost too high for now).

Be polite: sequential requests, timeouts, retries with backoff, a real User-Agent, per-source rate limits.

### 5.2 Deduplicate
- Primary key: canonical URL.
- Cross-source key: hash of normalized company + normalized title + normalized first ~500 characters of the description (lowercase, punctuation stripped, legal suffixes like LLC/Inc/Ltd/ТОВ removed). Company + title alone is not enough (same company can post the same title for different projects).
- Seen postings are never re-scored.

### 5.3 Prefilter (no LLM)
- Category allowlist.
- Title/description stop-words for seniority (Senior, Lead, Principal, Head, Architect, "5+ years", etc.), configurable, relaxed in `wide` mode.
- No country filter.

### 5.4 Score (LLM, `bulk`)
- Input: posting + the relevant CV track(s) + short instructions.
- Use OpenRouter structured outputs (JSON schema). Validate with Pydantic. On failure: retry (max 2), then fallback model, then mark `score_failed`.
- Disable or minimize model reasoning for this step (check current OpenRouter parameter names); reasoning tokens are billed as output.
- Output schema:
  - `fit_score` (0–100)
  - `verdict` (`apply` / `maybe` / `skip`)
  - `matched_skills`, `missing_skills`
  - `seniority_match`
  - `required_languages`, `language_mismatch` (bool)
  - `relocation_offered` (bool / unknown)
  - `remote_type`
  - `salary` (if stated)
  - `recommended_cv_track`
  - `rationale` (≤ 2 sentences)
- Relocation offered is a positive signal. Language mismatch never drops a posting; it only sets the flag.

### 5.5 Telegram report
After scoring, send a digest (counts per source, per verdict), then one card per posting above threshold: title, company, link, score, matched/missing skills, flags (language, relocation). Buttons: `Prepare package`, `Skip`.

### 5.6 Package (Phase 2)
Triggered by `Prepare package`.

1. **CV tailoring (LLM, `quality`).** Select the CV track; propose text edits for this posting. Hard rules:
   - Use only facts present in `facts.yaml`. Never invent projects, employers, dates, tools or numbers.
   - A bullet contains a metric only if that metric exists in `facts.yaml`; otherwise it is written without one.
   - The model outputs structured content (sections/bullets), not layout.
2. **Render.** Fill a Typst template with the structured content and compile with the `typst` CLI to PDF.
3. **ATS checks.**
   - Every render: extract the PDF text layer (pypdf or pdftotext) and assert name, email, phone and links are present; sections appear in the expected order; key terms from the posting appear verbatim (no ligature breakage); no missing text.
   - On template change (`job render-test`): additionally run the OpenResume parser (open source, AGPL-3.0, TypeScript; single-column English resumes) via a Node script and report extracted fields.
4. **Cover letter (LLM, `quality`).** Written in the posting's language. Facts only from `facts.yaml`. No em-dashes. No generic contrastive filler constructions. No flattery or buzzwords. For Ukrainian formal messages: open with "Доброго дня"; no "Дякую" / "З повагою" sign-off.
5. **LinkedIn outreach.** Generate a LinkedIn people-search URL for the company (recruiters / hiring roles) plus a draft message (≤ 300 characters). No scraping, no automated sending, no login.
6. Send the package to Telegram: PDF, letter, outreach draft with the search link, and buttons `Approve`, `Regenerate`, `Skip`.

### 5.7 Application forms (Phase 3)
- **Known ATS** (Workable, Greenhouse, Lever, Teamtailor first): Playwright fillers per ATS. Fixed fields come from `answers.yaml`. Free-text questions are drafted by the `quality` model under the same fact rules.
- **Knockout questions** (work authorization, time zone, years of experience, relocation, salary, etc.) are answered only from `answers.yaml`, never generated. If a required answer is missing, stop and ask in Telegram.
- **Before submit:** never click Submit automatically. Send to Telegram a screenshot plus a field → value list. The user can reply with free-text corrections; the `quality` model converts them into field changes, the form is updated and re-sent. Corrections apply to this application only and are never written back to `answers.yaml`. `Approve` submits.
- **Unknown sites:** send a field → value map, the PDF and the letter for manual filling. An experimental LLM browser agent (e.g. browser-use) may be added later behind a flag.

### 5.8 Tracking
SQLite statuses: `new`, `filtered_out`, `scored`, `score_failed`, `skipped`, `package_ready`, `form_filled`, `submitted`, `manual`, `replied`, `rejected`, `interview`. Store timestamps and per-call LLM cost (from OpenRouter usage data).

Bot commands: `/pending`, `/stats`, `/mark <id> <status>` (for replies/rejections/interviews reported manually).

`job stats` and `/stats` report: postings collected, deduplicated, scored, packages prepared, applications submitted, response rate, time per application, LLM spend. These numbers are real and may later be used as project metrics.

## 6. LLM layer

- Single OpenRouter client (OpenAI-compatible API). Per-request `max_price`. Retries with backoff on 429/5xx. Fallback model list per tier.
- Prompts live in versioned files, not inline strings.
- Account setting (manual, documented in README): deny training for free models. A `No endpoints found matching your data policy` error means that endpoint trains on data.
- Free `:free` variants: 20 requests/minute, 1000/day after a one-time purchase of ≥ 10 credits; the limit is account-wide. Throttle accordingly.

## 7. Model evaluation (`job eval`)

- A fixed test set of 20 real postings saved from the sources (stored under `data/`, not committed; a small anonymized sample may be committed as a fixture).
- Runs every model in `eval_candidates` on scoring, and on cover-letter generation for a subset.
- Reports per model: JSON/schema validity rate, retries needed, latency, cost; saves letters side by side for manual review.
- Initial candidates (IDs taken from the OpenRouter models API on 2026-09-15; re-check before use):
  - `z-ai/glm-5.3-flash`
  - `deepseek/deepseek-v4.1-flash`
  - `qwen/qwen3.8-flash`
  - `google/gemma-4-26b-a4b-it` (paid and `:free`; the free variant has no structured-output support, so validate JSON from the prompt)
- The eval command is a permanent feature: models change often, and switching tiers must only require a config edit plus one eval run.

## 8. CV template (Typst)

Until the user provides the final design, build a minimal template that follows these rules:
- A4, single column, no tables or multi-column layout, no icons, no photo, no date of birth, no street address.
- Standard section headings: Summary, Technical Skills, Projects, Experience, Education, Languages.
- Contact line inside the body (not in header/footer): city, country, time zone, email, phone, LinkedIn, GitHub as text links.
- Body text 10–11 pt, system/embedded fonts with full Unicode (Latin + Cyrillic).
- Ligatures disabled.
- Languages with CEFR levels.
- Dates in one consistent format.
- Content and layout are separate: the template reads a data file, so the design can later be replaced without touching the pipeline.

## 9. Stack (defaults, may change with justification)

Python 3.12, uv, Typer, Pydantic v2, httpx, feedparser, python-jobspy, python-telegram-bot or aiogram, sqlite3, pypdf, Playwright, typst CLI, pytest. Node only for the OpenResume check.

## 10. Quality

- pytest for every module; sources tested with recorded fixtures, no live network in unit tests.
- Type hints, ruff.
- README: setup, `.env`, OpenRouter privacy setting, how to add a source, how to switch models, how to run eval.

## 11. Phases

1. **Phase 1:** project skeleton, config, DB, sources (Djinni first, then DOU, Remote OK, WWR, LinkedIn), dedup, prefilter, scoring, Telegram digest and cards, `/pending`, `job eval`, `job stats`.
2. **Phase 2:** facts registry loader, CV tailoring, Typst template and render, ATS checks, cover letters, LinkedIn outreach drafts, package flow in Telegram.
3. **Phase 3:** answer bank, ATS form fillers with the Telegram correction/approval loop, manual-fill fallback, NoFluffJobs if feasible.

## 12. Non-goals

- Automated LinkedIn actions of any kind (messaging, connecting, scraping with an account).
- Submitting any form without explicit approval in Telegram.
- Generating answers to knockout questions.
- Work.ua / Robota.ua scraping.
- Committing personal data.
