#!/usr/bin/env bash
# Run on each sandbox node during provisioning; restart k3s explicitly afterwards.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CDIR=/var/lib/rancher/k3s/agent/etc/containerd
[ "$(id -u)" = 0 ] || { echo 'run as root' >&2; exit 1; }
command -v runsc >/dev/null
command -v containerd-shim-runsc-v1 >/dev/null
# Never overwrite an operator's custom template. Older nodes may already import drop-ins.
if ! grep -q 'config-v3.toml.d' "$CDIR/config.toml"; then
  if [ -e "$CDIR/config-v3.toml.tmpl" ]; then
    cmp -s "$ROOT/config-v3.toml.tmpl" "$CDIR/config-v3.toml.tmpl" || {
      echo 'custom containerd template: add the runsc drop-in import manually' >&2; exit 1;
    }
  fi
  install -m 0644 "$ROOT/config-v3.toml.tmpl" "$CDIR/config-v3.toml.tmpl"
fi
install -D -m 0644 "$ROOT/runsc-options.toml" /etc/containerd/runsc.toml
install -D -m 0644 "$ROOT/runsc.toml" "$CDIR/config-v3.toml.d/runsc.toml"
install -D -m 0755 "$ROOT/pod-icmp-deny.sh" /usr/local/sbin/ailab-pod-icmp-deny
install -D -m 0644 "$ROOT/ailab-pod-icmp-deny.service" /etc/systemd/system/ailab-pod-icmp-deny.service
systemctl daemon-reload
systemctl enable --now ailab-pod-icmp-deny.service
echo 'runsc configured: restart k3s before accepting new sandboxes (existing ones retain old limits)'
