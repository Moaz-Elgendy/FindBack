#!/usr/bin/env bash
# Generates the native Android/iOS projects around the hand-written Dart
# sources, then installs packages and runs the checks CI runs.
#
# The Flutter tree in this repo is checked in without android/ and ios/ because
# those directories are generated, machine-specific, and 20k lines of noise.
# `flutter create` is run in a temp dir and only the platform folders are copied
# in, so lib/ and pubspec.yaml are never overwritten by template files.
#
#   tool/setup_mobile.sh                 # android + ios
#   FINDBACK_EXTRA_PLATFORMS=linux tool/setup_mobile.sh
#
# Re-running is safe: existing native source files are left untouched.
set -euo pipefail

MOBILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/mobile"
ORG="${FINDBACK_ORG_ID:-com.findback}"
platforms="android,ios"
[[ -n "${FINDBACK_EXTRA_PLATFORMS:-}" ]] && platforms="android,ios,$FINDBACK_EXTRA_PLATFORMS"

command -v flutter >/dev/null 2>&1 || {
  echo "flutter is not on PATH. Install it from https://docs.flutter.dev/get-started/install" >&2
  exit 1
}
[[ -f "$MOBILE/pubspec.yaml" ]] || { echo "missing $MOBILE/pubspec.yaml" >&2; exit 1; }

if [[ ! -f "$MOBILE/android/gradlew" || ! -f "$MOBILE/ios/Runner.xcodeproj/project.pbxproj" ]]; then
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  echo "generating platform projects ($platforms) for org $ORG"
  (cd "$tmp" && flutter create --org "$ORG" --project-name findback \
      --platforms "$platforms" --no-pub findback >/dev/null)
  for dir in android ios linux windows macos web; do
    if [[ -d "$tmp/findback/$dir" ]]; then
      mkdir -p "$MOBILE/$dir"
      cp -Rn "$tmp/findback/$dir/." "$MOBILE/$dir/"
    fi
  done
  echo "created: $(cd "$MOBILE" && ls -d android ios linux 2>/dev/null | tr '\n' ' ')"
else
  echo "platform projects already present, skipping generation"
fi

cd "$MOBILE"
flutter pub get
flutter analyze
flutter test

cat <<'EOF'

Ready. Run the API first (see README), then:

  cd mobile
  flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000

10.0.2.2 is how an Android emulator reaches the host machine's localhost; on a
iOS simulator use http://localhost:8000.
EOF
