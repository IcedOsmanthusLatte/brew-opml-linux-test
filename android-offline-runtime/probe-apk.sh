#!/usr/bin/env bash
set -euo pipefail
d="$(cd "$(dirname "$0")" && pwd)"
p="$RUNNER_TEMP/brew-android-probe"
mkdir -p "$p/classes" "$p/dex"
tools="$(find "$ANDROID_HOME/build-tools" -name aapt -type f | sort -V | tail -1 | xargs dirname)"
android="$ANDROID_HOME/platforms/android-35/android.jar"
javac -source 8 -target 8 -cp "$android" -d "$p/classes" "$d/Probe.java"
"$tools/d8" --lib "$android" --output "$p/dex" $(find "$p/classes" -name '*.class')
"$tools/aapt" package -f -M "$d/ProbeManifest.xml" -I "$android" -F "$p/raw.apk"
(cd "$p/dex" && "$tools/aapt" add "$p/raw.apk" classes.dex)
"$tools/zipalign" -p -f 4 "$p/raw.apk" "$p/aligned.apk"
keytool -genkeypair -keystore "$p/key.jks" -storepass localproof -keypass localproof -keyalg RSA -keysize 2048 -alias local -dname "CN=Brew ephemeral guest test" -validity 2
"$tools/apksigner" sign --ks "$p/key.jks" --ks-pass pass:localproof --out "$p/probe.apk" "$p/aligned.apk"
adb install "$p/probe.apk"
