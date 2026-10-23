#!/usr/bin/env bash
# Independent PostgreSQL credential for the dev overlay; no output contains the password.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
if $KUBECTL -n platform get secret platform-database >/dev/null 2>&1; then exit 0; fi
password=$(openssl rand -hex 24)
$KUBECTL -n platform create secret generic platform-database --from-literal="password=$password" >/dev/null
