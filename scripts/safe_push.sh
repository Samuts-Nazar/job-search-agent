#!/usr/bin/env bash
# Shared "commit + safe push" helper for the private data repo's small
# git-tracked text files (telegram_offset.txt, the deadman alert flag,
# etc). See the GitHub Actions deployment plan, amendment 3: both
# pipeline.yml and telegram-poll.yml write here, serialized by a shared
# concurrency group -- but as defense in depth, this refuses to push
# (rather than merge or force-push) if the remote moved since checkout.
#
# Usage: safe_push.sh <repo-dir> <commit-message>
set -euo pipefail

REPO_DIR="$1"
COMMIT_MESSAGE="$2"

cd "$REPO_DIR"

if [ -z "$(git status --porcelain)" ]; then
  echo "No changes to commit."
  exit 0
fi

BASE_SHA="$(git rev-parse HEAD)"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

git add -A
git commit -m "$COMMIT_MESSAGE"

git fetch origin "$BRANCH"
REMOTE_SHA="$(git rev-parse "origin/$BRANCH")"

if [ "$REMOTE_SHA" != "$BASE_SHA" ]; then
  echo "ERROR: origin/$BRANCH moved since checkout ($BASE_SHA -> $REMOTE_SHA)." >&2
  echo "Refusing to push -- investigate the concurrent write rather than force-pushing." >&2
  exit 1
fi

git push origin "$BRANCH"
