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
uv run job --no-telegram      # same run, but sends and polls nothing (collect + score only)
uv run job eval         # compare models in config.yaml -> models.eval_candidates
uv run job stats        # print pipeline/spend stats + extended aggregates (skills, reply rate, salary, ...)
uv run job check-data   # validate data/facts.yaml and data/answers.yaml (also run before every `job`)
uv run job doctor       # read-only check of everything a real run needs (data files, config/secrets,
                         # OpenRouter key + credit balance, Telegram bot + chat id, typst/node,
                         # every enabled source, DB schema) -- run this first when something looks broken
uv run job resume       # finish any posting left in status='new' by an interrupted prior run;
                         # `job` also runs this automatically first, so this is mainly for manual checks
uv run job render-test  # render the CV template against data.example content, run ATS + OpenResume checks
uv run job export       # dump postings/scores/applications to CSV in output/export/ for outside analysis
uv run job analyze --rescore  # re-score every stored posting with the current model/prompt
                               # as new scoring_runs rows, without touching the original result
uv run job analyze --readmit  # re-check filtered-out postings against the current prefilter
                               # (e.g. after a prefilter fix) and score any that now pass
uv run job analyze --detect-ats  # for postings at/above the active fit_score threshold not yet
                               # checked, resolve and store which ATS the apply link leads to
                               # (deferred auto-fill decision data, not automated filling itself)
uv run job telegram-cleanup --below 40  # delete the bot's own cards for postings scored under 40
                                         # (Telegram only allows deleting messages <48h old)
```

Which scored postings get a Telegram card is controlled by `telegram.min_score`
in config.yaml -- independent of `thresholds.*.fit_score_min`/`maybe_score_min`,
which only set the apply/maybe/skip bands shown on each card (see
`job_agent/scoring.py:derive_verdict`).

## Data collected beyond the fit score

Scoring also extracts posting metadata that doesn't affect pipeline
behavior yet, purely so it's there for later analysis: tech stack (flat
list, and a richer `tech_tags` list tagged required/nice_to_have),
required years of experience, stated seniority level, work format,
country, salary breakdown (min/max/currency/period), portfolio/GitHub/
test-task expectations, and company type. Every one of these fields is
optional -- the model never fails validation for not finding one. The
apply/company URL is also checked against a small list of known ATS
domains (Greenhouse, Lever, Workable, Teamtailor, ...; see
`ats_vendor.py`) with no network request.

Postings dropped by the prefilter keep the reason (`filtered_reason`:
`category_not_allowed` or `seniority_stopword:<word>` / `seniority_years:<n>`)
instead of just a bare "filtered_out" status. Every scored posting's full
result is stored regardless of whether it's above threshold, and each
posting's raw source payload (every field the source gave us, not just
what we normalize) is kept in `raw_payload` so it can be re-analyzed later
without re-scraping.

Every scoring attempt (including `job analyze --rescore` re-scores) is
recorded as its own row in `scoring_runs`, tagged with the exact model and
prompt version used -- the `postings` table's own fit_score/verdict/etc.
columns only ever reflect the first ("primary") successful score, which is
what the Telegram digest and `job stats` show.

`job stats` also reports tech-tag co-occurrence (top pairs/triples),
per-tag frequency/required-share/median-salary/junior-share, a monthly
posting trend per tag, and the share of postings expecting a GitHub
profile / portfolio / take-home test.

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

## Deploying on GitHub Actions (run from your phone)

`job` also runs unattended on GitHub Actions, with Telegram as the only
interface -- no server of your own needed. Two repos are involved: this
public code repo (workflows + code, no personal data ever) and a private
data repo you create yourself (config, facts, answers, `jobs.db`).
Multiple people can run their own copy by forking this repo and pointing
it at their own private data repo -- nothing owner-specific is hardcoded
anywhere in the workflows or code.

Setup for your own fork:

1. **Create a private data repo.** Any name, e.g. `job-search-data`.
   Copy `config.example.yaml` to `config.yaml` and `data.example/*` to
   `data/*` in it, then edit them with your real values (same files you'd
   use locally -- see [Setup](#setup) above). Commit and push.
2. **Generate a fine-grained PAT** scoped to just that private repo, with
   `contents: read and write` permission (this also covers uploading/
   downloading the `jobs.db` release asset -- see below).
3. **Set these secrets** on your fork of this repo (Settings -> Secrets
   and variables -> Actions):

   | Secret | Value |
   |---|---|
   | `OPENROUTER_API_KEY` | same as your local `.env` |
   | `TELEGRAM_BOT_TOKEN` | same as your local `.env` |
   | `TELEGRAM_CHAT_ID` | same as your local `.env` |
   | `DATA_REPO` | `your-username/job-search-data` |
   | `DATA_REPO_TOKEN` | the PAT from step 2 |
   | `HEALTHCHECK_URL` | optional -- see below |

4. **Confirm Actions is enabled** on your fork (forks sometimes have it
   off by default).
5. Adjust the cron schedule in `.github/workflows/pipeline.yml` if you
   want a different time than the default daily run.

What each workflow does:

- **`pipeline.yml`** -- daily (or `/run` from Telegram, or manual
  dispatch): collects, scores, sends the Telegram digest, then exits
  (`--no-poll`) rather than blocking like local `job` does.
- **`telegram-poll.yml`** -- every 15 minutes: processes whatever
  Telegram commands/button presses came in since last time
  (`/pending`, `/stats`, `/mark`, `/run`). Telegram keeps updates for
  24h, so a 15-minute gap never loses one.
- **`keepalive.yml`** -- monthly no-op commit to *this* repo. GitHub
  auto-disables a public repo's scheduled triggers after 60 days with no
  commits to that repo, and every real commit this deployment makes goes
  to your *private* data repo instead -- this is just what keeps the
  schedule alive.

**`jobs.db` is not committed to git** -- a constantly-changing SQLite
file would make your private repo's history grow without bound. It's
stored as a rotating GitHub Release asset on the private repo instead
(`job db-pull`/`job db-push`, keeping the newest 7 versions); everything
else (config, facts, answers, the Telegram update offset) is small text
committed normally.

**Dead-man's switch (optional but recommended):** set `HEALTHCHECK_URL`
to a check URL from a free heartbeat service (e.g.
[healthchecks.io](https://healthchecks.io)) with its Telegram integration
enabled and a 36h grace period. `pipeline.yml` pings it on every
successful run; the service alerts you if a ping doesn't arrive in time
-- this works even if GitHub Actions itself is down or your workflows got
disabled, which an in-repo check alone couldn't detect. `job
telegram-process` also runs its own secondary check reading `jobs.db`
directly, as a backup signal for "Actions is fine but the pipeline itself
keeps failing."

**Public-repo logs are world-readable.** Both scheduled workflows set
`JOB_CI=1`, which makes `job` print only counts, source names, error
types, and timings -- never posting text, fact values, or full exception
messages. No `actions/upload-artifact` step runs in either workflow
either, so nothing from a run is attached as a downloadable artifact.

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
