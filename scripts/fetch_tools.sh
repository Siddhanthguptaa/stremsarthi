#!/usr/bin/env bash
# Download MediaMTX into .tmp/tools/ and verify it against the release's published checksums.
# The first successful run pins the SHA-256 in models/tools.lock.json (committed); every later
# run refuses a mismatch. Extracts into a fresh directory; nothing is executed before verification.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
LOCK="models/tools.lock.json"
VERSIONS=(1.21.4 1.21.3)
mkdir -p .tmp/tools models

pinned_version=""; pinned_sha=""
if [ -f "$LOCK" ]; then
  pinned_version="$(python3 -c "import json;d=json.load(open('$LOCK'));print(d['mediamtx']['version'])")"
  pinned_sha="$(python3 -c "import json;d=json.load(open('$LOCK'));print(d['mediamtx']['sha256'])")"
  VERSIONS=("$pinned_version")
fi

for ver in "${VERSIONS[@]}"; do
  dest=".tmp/tools/mediamtx-v${ver}"
  if [ -x "$dest/mediamtx" ]; then
    echo "mediamtx v${ver} already present"
    exit 0
  fi
  name="mediamtx_v${ver}_linux_amd64.tar.gz"
  base="https://github.com/bluenviron/mediamtx/releases/download/v${ver}"
  tmp="$(mktemp -d)"
  if ! curl -fsSL -o "$tmp/$name" "$base/$name"; then
    echo "v${ver} not downloadable, trying next"; rm -rf "$tmp"; continue
  fi
  actual="$(sha256sum "$tmp/$name" | cut -d' ' -f1)"
  if [ -n "$pinned_sha" ]; then
    [ "$actual" = "$pinned_sha" ] || { echo "SHA-256 mismatch for $name: refusing" >&2; rm -rf "$tmp"; exit 1; }
  else
    curl -fsSL -o "$tmp/checksums.sha256" "$base/checksums.sha256" || { echo "no published checksums for v${ver}" >&2; rm -rf "$tmp"; continue; }
    # format: "<sha256> *<file>" (binary-mode marker) or "<sha256>  <file>"
    expected="$(awk -v n="$name" '{f=$2; sub(/^\*/, "", f); if (f == n) print $1}' "$tmp/checksums.sha256" | head -1)"
    [ -n "$expected" ] || { echo "no checksum entry for $name" >&2; rm -rf "$tmp"; continue; }
    [ "$actual" = "$expected" ] || { echo "SHA-256 does not match the published checksum: refusing" >&2; rm -rf "$tmp"; exit 1; }
    printf '{\n  "mediamtx": {"version": "%s", "url": "%s/%s", "sha256": "%s"}\n}\n' "$ver" "$base" "$name" "$actual" > "$LOCK"
    echo "pinned mediamtx v${ver} sha256=$actual in $LOCK"
  fi
  mkdir -p "$dest"
  tar -xzf "$tmp/$name" -C "$dest"
  rm -rf "$tmp"
  echo "installed $dest"
  exit 0
done
echo "could not install MediaMTX" >&2
exit 1
