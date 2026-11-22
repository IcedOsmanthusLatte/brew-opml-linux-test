#!/usr/bin/env bash
set -euo pipefail
d="$(cd "$(dirname "$0")" && pwd)"
pkg="system-images;android-35;google_apis;x86_64"
sdkmanager "$pkg" "platforms;android-35"
echo no | avdmanager create avd --force -n brew_android -k "$pkg"
emulator -avd brew_android -no-window -no-audio -no-boot-anim -no-snapshot-load -no-snapshot-save -gpu swiftshader_indirect -accel on > "$RUNNER_TEMP/brew-emulator.log" 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
timeout 200 adb wait-for-device
timeout 140 bash -c 'until [[ $(adb shell getprop sys.boot_completed | tr -d "\r") == 1 ]]; do sleep 3; done'
adb shell input keyevent 82
adb shell settings put global window_animation_scale 0
adb shell settings put global transition_animation_scale 0
adb shell settings put global animator_duration_scale 0
adb shell settings put secure navigation_mode 0
adb install "$RUNNER_TEMP/brew-fdroid/com.nononsenseapps.feeder_4191.apk"
adb install "$RUNNER_TEMP/brew-fdroid/com.prof18.feedflow_6239.apk"
adb push "$d/control.opml" /sdcard/Download/brew-control.opml
adb shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file:///sdcard/Download/brew-control.opml
bash "$d/probe-apk.sh"
python "$d/bootstrap.py"
if [ -f "$d/experiment.py" ]; then python "$d/experiment.py"; fi
