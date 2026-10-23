#!/usr/bin/env bash
# Build the real S3 contract fixture from a fixed source archive; cache the output directory in CI.
set -euo pipefail
: "${CI_MINIO_DIR:?set CI_MINIO_DIR to the binary cache directory}"
source_dir=$(mktemp -d)
trap 'rm -rf "$source_dir"' EXIT
curl -fsSL --retry 3 --connect-timeout 10 --max-time 120 \
  https://codeload.github.com/minio/minio/tar.gz/refs/tags/RELEASE.2025-04-22T22-12-26Z \
  -o "$source_dir/source.tar.gz"
printf '%s  %s\n' 1a63209927b9d1fb239b2a776b7b01d1902eff19379a108f4bc7005edcc0968c "$source_dir/source.tar.gz" | sha256sum -c -
tar -xzf "$source_dir/source.tar.gz" -C "$source_dir"
mkdir -p "$CI_MINIO_DIR"
cd "$source_dir/minio-RELEASE.2025-04-22T22-12-26Z"
CGO_ENABLED=0 go build -p 2 -trimpath -o "$CI_MINIO_DIR/minio" .
"$CI_MINIO_DIR/minio" --version
