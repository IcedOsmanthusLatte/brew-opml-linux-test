#!/bin/bash
set -euo pipefail

app="$1"; mode="$2"; round="$3"
case "$mode" in baseline|nested|flattened|unique) ;; *) exit 2 ;; esac
[[ "$round" = 1 || "$round" = 2 ]]
scratch=/tmp/brew-opml-runtime
proof="$GITHUB_WORKSPACE/proof"
mkdir -p "$scratch/apps" "$proof" "$scratch/export"
[[ "$(uname -m)" = arm64 ]]
sw_vers > "$proof/macos.txt"
xcodebuild -version > "$proof/xcode.txt"
case "$app" in
  netnewswire)
    url='https://github.com/Ranchero-Software/NetNewsWire/releases/download/mac-7.3.1/NetNewsWire7.3.1.zip'
    digest='280ffd15e2df86a39146410d362f2bafca2c559f33cab78cd538669e96245ab9'
    expected=7.3.1; bundle="$scratch/apps/NetNewsWire.app"; suffix=zip
    ;;
  vienna)
    url='https://github.com/ViennaRSS/vienna-rss/releases/download/v/3.10.8/Vienna3.10.8.dmg'
    digest='6833fecb70c23c1ba98868086e2245c9a05a4f5a4960b019b64ca3728aaa85bd'
    expected=3.10.8; bundle="$scratch/apps/Vienna.app"; suffix=dmg
    ;;
  *) exit 2 ;;
esac
asset="$scratch/source.$suffix"
curl --fail --retry 3 --location --max-time 60 --silent --show-error "$url" --output "$asset"
printf '%s  %s\n' "$digest" "$asset" | shasum -a 256 --check
if [[ "$suffix" = zip ]]; then
  ditto -x -k "$asset" "$scratch/apps"
else
  volume="$scratch/volume"
  mkdir "$volume"
  hdiutil attach "$asset" -readonly -nobrowse -mountpoint "$volume" >/dev/null
  trap 'hdiutil detach "$volume" >/dev/null || true' EXIT
  ditto "$volume/Vienna.app" "$bundle"
  hdiutil detach "$volume" >/dev/null
  trap - EXIT
fi
info="$bundle/Contents/Info.plist"
version="$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$info")"
bundle_id="$(/usr/libexec/PlistBuddy -c 'Print CFBundleIdentifier' "$info")"
build="$(/usr/libexec/PlistBuddy -c 'Print CFBundleVersion' "$info")"
[[ "$version" = "$expected" || "$version" = "$expected "* ]]
codesign --verify --deep --strict "$bundle"
codesign -dv --verbose=2 "$bundle" 2>&1 | grep -E '^(Identifier|Authority|TeamIdentifier)=' > "$proof/signature.txt"
file "$bundle/Contents/MacOS/"* > "$proof/architecture.txt"
python3 mac-opml-runtime/fixtures.py "$scratch" "$proof" "$app" "$mode" "$round" "$bundle" "$bundle_id" "$version" "$build" "$url" "$digest"
