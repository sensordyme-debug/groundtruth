#!/usr/bin/env bash
# Read-only: metadata for given commits + raw reflog lines + git identities in WSL.
cd "$(dirname "$0")/../.."
for c in "$@"; do
  git show -s --date=iso-strict --format='commit:    %H%nauthor:    %an <%ae> %ad%ncommitter: %cn <%ce> %cd%nsubject:   %s%ntrailers:  %(trailers:only,unfold)' "$c"
  git show --stat --format= "$c" | tail -1
  echo "raw reflog line:"; grep "$(git rev-parse "$c")" .git/logs/HEAD | sed -E 's/^[0-9a-f]+ [0-9a-f]+ //' | cut -c1-140
  echo ---
done
echo "WSL global identity: $(git config --global user.name) <$(git config --global user.email)>"
echo "repo-local identity: $(git config --local user.name) <$(git config --local user.email)>"
