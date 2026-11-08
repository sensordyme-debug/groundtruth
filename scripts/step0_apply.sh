#!/usr/bin/env bash
set -e
cd /mnt/c/Users/orynt/GeoSpatial/gt-redesign
# 0.2: the classic UI goes back to exactly demo-final; backend work stays
git rm -r -q --cached web/src web/shared web/index.html >/dev/null
rm -rf web/src web/shared
git checkout demo-final -- web/src web/shared web/index.html
git add web/src web/shared web/index.html
git commit -q -m "Restore the classic UI to demo-final (rejected redesign UI removed); backend work kept

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
echo "classic UI restored: $(git log --oneline -1)"
git diff --stat demo-final -- web/src web/shared web/index.html | tail -1
# 0.1: the approved design target
mkdir -p docs/design/target
D=/mnt/c/Users/orynt/Downloads
cp "$D/DESIGN_TARGET.md" docs/design/target/DESIGN_TARGET.md
cp "$D/Main.dc.html" docs/design/target/Main.dc.html
cp "$D"/Groundtruth\ Conversational\ Map*.html docs/design/target/mockup.html
git add docs/design/target && git commit -q -m "design: approved UI target (mockup + spec)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
echo "design committed: $(git log --oneline -1)"; ls -la docs/design/target
echo "== branch base vs demo-final"; git merge-base --is-ancestor demo-final HEAD && echo "demo-final is an ancestor"
git log --oneline demo-final..HEAD | cat
