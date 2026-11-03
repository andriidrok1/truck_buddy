#!/usr/bin/env bash
# Sign the newest IPA from the "iOS unsigned IPA" workflow with a free Apple ID and install it on the iPhone
# plugged in over USB. Same route as glasses-tutor: plumesign (https://github.com/claration/impactor/releases),
# usbmuxd running, `plumesign account login -u <apple id>` once. A free profile lasts 7 days.
# plumesign's --udid does not match connected devices (v2.7.2): its menu runs under a pty and Enter picks the
# first connected iPhone.
set -euo pipefail

PLUMESIGN="${PLUMESIGN:-$HOME/Applications/impactor/plumesign-linux-x86_64}"
REPO="andriidrok1/truck_buddy"
BRANCH="${1:-claude/app-ios-ipa}"
WORK="$(mktemp -d)"

RUN=$(gh run list -R "$REPO" -w "iOS unsigned IPA" -b "$BRANCH" -s success -L 1 --json databaseId -q '.[0].databaseId')
test -n "$RUN" || { echo "no successful build on $BRANCH"; exit 1; }
echo "→ downloading TruckBuddy.ipa from run $RUN"
gh run download "$RUN" -R "$REPO" -n TruckBuddy-ipa -D "$WORK"

echo "→ signing with Apple ID and installing on the first connected iPhone"
export RUST_LOG="${RUST_LOG:-info}"
script -qec "$PLUMESIGN sign -p $WORK/TruckBuddy.ipa --apple-id --register-and-install -o $WORK/TruckBuddy-signed.ipa" /dev/null <<< $'\n\n\n' \
  | sed 's/\x1b\[[0-9;]*m//g; s/\x1b\[?25[lh]//g' | grep -E "INFO|WARN|ERROR|Error|Select" || true

test -f "$WORK/TruckBuddy-signed.ipa" && echo "signed IPA: $WORK/TruckBuddy-signed.ipa"
