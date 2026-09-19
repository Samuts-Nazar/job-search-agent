# Job Search Agent

A local, CLI-driven job search agent. On each manual run it collects job postings
from several sources, deduplicates them, filters and scores them against the
candidate's CVs with a cheap LLM, prepares application packages (tailored CV PDF,
cover letter, LinkedIn outreach draft, form-fill data) with a stronger LLM, and
routes every decision through a Telegram bot for human approval.

See [`PROJECT.md`](./PROJECT.md) for the full specification.

**Status:** Phase 1 in progress (collection, dedup, prefilter, scoring, Telegram
digest, `job eval`, `job stats`, `job check-data`, `job render-test`). No
personal data is ever committed to this repository.

## Setup

1. Install [Nix](https://nixos.org/download) with flakes enabled (or install
   Python 3.12, [uv](https://docs.astral.sh/uv/), and [ruff](https://docs.astral.sh/ruff/) yourself).
2. Enter the dev shell: `nix develop`
3. Install dependencies: `uv sync`
4. Copy the example config and data files:
   ```
   cp config.example.yaml config.yaml
   cp -r data.example data
   ```
5. Create `.env` (never committed) with:
   ```
   OPENROUTER_API_KEY=...
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```
6. Edit `config.yaml`: set `models.bulk` / `models.quality` to real OpenRouter
   model IDs (verify current IDs at https://openrouter.ai/models), and fill in
   `data/facts.yaml` / `data/answers.yaml` with real candidate data (see
   `data.example/` for the expected shape). Set `example: false` (or delete
   that line) in both files, then run `uv run job check-data` -- `job`
   refuses to run otherwise. It also refuses if any identity-like field
   (name, email, phone, LinkedIn/GitHub links, employer, institution,
   project name) still literally matches the example file, or if a
   required field (including every knockout field in `answers.yaml`) is
   missing -- it prints exactly which fields.
7. One-time, only if you'll run `job render-test` (CV template check): install
   the OpenResume parser's Node dependencies:
   ```
   cd src/job_agent/cv/openresume && npm install && cd -
   ```

### OpenRouter privacy setting

In your OpenRouter account settings, disable "allow training on my data" for
free-tier (`:free`) models. If a request fails with
`No endpoints found matching your data policy`, that endpoint trains on data
and must be avoided or the setting must be re-checked.

## Running

```
uv run job              # full pipeline run, then stays up for Telegram interaction (Ctrl+C to stop)
uv run job eval         # compare models in config.yaml -> models.eval_candidates
uv run job stats        # print pipeline/spend stats + extended aggregates (skills, reply rate, salary, ...)
uv run job check-data   # validate data/facts.yaml and data/answers.yaml (also run before every `job`)
uv run job render-test  # render the CV template against data.example content, run ATS + OpenResume checks
uv run job export       # dump postings/scores/applications to CSV in output/export/ for outside analysis
```

## Data collected beyond the fit score

Scoring also extracts posting metadata that doesn't affect pipeline
behavior yet, purely so it's there for later analysis: tech stack,
required years of experience, stated seniority level, work format,
country, and a salary breakdown (min/max/currency/period). Every one of
these fields is optional -- the model never fails validation for not
finding one. The apply/company URL is also checked against a small list of
known ATS domains (Greenhouse, Lever, Workable, Teamtailor, ...; see
`ats_vendor.py`) with no network request.

Postings dropped by the prefilter keep the reason (`filtered_reason`:
`category_not_allowed` or `seniority_stopword:<word>` / `seniority_years:<n>`)
instead of just a bare "filtered_out" status. Every scored posting's full
result is stored regardless of whether it's above threshold.

An `applications` table (plus a status-history table) exists for Phase
3's form-fill/submission flow to write into later -- nothing in Phase 1/2
creates rows there yet.

## Adding a source

Add a module under `src/job_agent/sources/` that returns a list of normalized
`Posting` objects (see `src/job_agent/sources/base.py`). Register it in
`config.yaml` under `sources:`. Unit tests must use recorded fixtures, not live
network calls (see `tests/sources/`).

## Switching models

Model IDs are read entirely from `config.yaml` (`models.bulk`, `models.quality`,
`models.fallbacks`, `models.eval_candidates`) -- no model name is hardcoded in
the source. Run `uv run job eval` after switching to confirm the new model's
JSON-schema validity rate, latency, and cost are acceptable.

## Development

```
uv run pytest
uv run ruff check .
```

## License

This project's own code is all rights reserved, (c) Nazar Samuts.

`src/job_agent/cv/openresume/` is third-party code under the GNU Affero
General Public License v3.0 -- see the `LICENSE` and `NOTICE.md` files in
that directory for exact provenance and licensing.
