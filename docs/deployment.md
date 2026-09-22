# Deploying on GitHub Actions

See the README's "Deploying on GitHub Actions" section for setup steps.
This file is the design rationale -- why the architecture looks the way
it does, for whoever maintains it next.

## Two-repo split

Code (this repo, public) and personal data (a private repo you create)
are split so the code can be shared/forked freely while nothing personal
-- `config.yaml`, `data/facts.yaml`, `data/answers.yaml`, `jobs.db` --
ever touches a public repo, its logs, or its artifacts.

Both workflows are defined in the *public* repo specifically because
GitHub Actions minutes are free and unlimited on standard GitHub-hosted
runners for any workflow defined in a public repository, regardless of
what it checks out or writes to during the run (verified live
2026-09-22: private repos get a 2,000 min/month free quota, but that only
applies to workflows *defined* in a private repo). Checking out and
writing to a private repo from a public-repo-owned job doesn't change
which repo the minutes are billed against.

## `jobs.db` lives outside git history

SQLite binaries compress and delta poorly in git -- a naive design that
commits the full DB on every run would add roughly 500MB-1.3GB to the
private repo's `.git` per month, compounding forever (measured basis:
~20KB/posting on the real DB, 30+ pipeline runs/month each with new
data, up to 2,880 poll runs/month before a "commit only on change"
guard). Instead, `job db-pull`/`job db-push`
(`src/job_agent/release_assets.py`) store it as a rotating GitHub Release
asset on the private repo -- each upload just replaces state, no history
to accumulate. The newest 7 versions are kept as rotating backups. Small
text files (config, facts, answers, the Telegram update offset, the
dead-man alert flag) stay committed to git normally -- they're tiny and
diffing them is exactly what git is good at.

## Dead-man's switch: external primary, in-repo secondary

A staleness check that only runs inside GitHub Actions can't detect
"GitHub Actions itself is broken" (both scheduled workflows silently
disabled, an outage, the concurrency queue stuck) -- it dies right along
with the thing it's supposed to catch. The primary signal is external: an
optional `HEALTHCHECK_URL` (e.g. healthchecks.io) that `pipeline.yml`
pings on every successful run, with the service's own independent
scheduler alerting via Telegram if a ping doesn't arrive within 36h.

The secondary signal (`src/job_agent/deadman.py`, run from `job
telegram-process`) reads `pipeline_runs` directly and alerts if the most
recent run is stale, deduplicated via a flag file so it only pages once
per episode. It catches a different failure mode: Actions is running
fine, but the pipeline itself keeps failing before completing (bad
credentials, a broken source) -- something a heartbeat that only fires on
success can't distinguish from "Actions is down" on its own.

## Scheduled-workflow auto-disable (60 days)

GitHub disables a public repo's *scheduled* triggers after 60 days with
no new commits *to that repo* -- only a real `git push`-created commit
resets the clock; workflow runs, tags, and PR merges don't count. Since
every real commit this design produces goes to the private data repo,
this (public) repo would see zero activity on its own indefinitely.
`keepalive.yml` is a monthly no-op commit (a timestamp, zero personal
content) specifically to reset that clock, with a wide safety margin.

## Concurrent writes

`pipeline.yml` and `telegram-poll.yml` both read-modify-write the same
private repo and the same `jobs.db` release asset. Both declare the same
`concurrency: { group: data-repo-writer, cancel-in-progress: false }`, so
GitHub queues one behind the other instead of running them at the same
time. `scripts/safe_push.sh` is defense in depth for the git side: it
fetches before pushing and fails loudly (not force-push, not merge) if
the remote moved since checkout.

## `/run` from Telegram

Telegram can't call the GitHub API directly. `telegram-poll.yml` is what
closes the loop: `job telegram-process` wires `telegram_bot.build_router`'s
`on_run` callback to `cli._trigger_pipeline_workflow()`, which calls
GitHub's `workflow_dispatch` REST endpoint for `pipeline.yml`. This needs
`permissions: actions: write` on `telegram-poll.yml` (set) and works with
the default `GITHUB_TOKEN` -- no extra PAT needed for this specific call.

## Cloudflare Worker webhook (future)

`telegram_bot.build_router`/`build_dispatcher` and their per-update
handler logic (`/pending`, `/stats`, `/mark`, buttons, `/run`) don't know
or care how an update arrived -- `job telegram-process` calls
`dispatcher.feed_update()` per update fetched via polling `getUpdates`,
but a future Cloudflare Worker webhook would only need to get each
incoming Telegram update into that same call. No change to the handler
logic itself, only to what delivers updates to it.
