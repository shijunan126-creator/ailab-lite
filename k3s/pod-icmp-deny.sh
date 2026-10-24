#!/usr/bin/env bash
# k3s kube-router permits ICMP before namespace policy evaluation. Block that
# channel on this IPv4-only node; TCP/UDP remain governed by NetworkPolicy.
set -euo pipefail
iptables -t raw -C PREROUTING -s 10.42.0.0/16 -p icmp -j DROP 2>/dev/null || \
  iptables -t raw -I PREROUTING 1 -s 10.42.0.0/16 -p icmp -j DROP
