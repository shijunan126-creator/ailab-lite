"""Fail deployment before accepting sandboxes on an unprovisioned lite node."""

import ipaddress
import json
import subprocess
import tomllib
from pathlib import Path


def output(*args):
    return subprocess.check_output(args, text=True, timeout=15)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


info = json.loads(output("k3s", "crictl", "info"))
options = info["config"]["containerd"]["runtimes"]["runsc"]["options"]
require(options["ConfigPath"] == "/etc/containerd/runsc.toml", "restart k3s with the provisioned runsc ConfigPath")
with Path(options["ConfigPath"]).open("rb") as file:
    config = tomllib.load(file)["runsc_config"]
require(config["overlay2"] == "root:self,size=4g", "runsc root overlay must be capped at 4 GiB")
require(config["net-raw"] == "false", "runsc raw sockets must be disabled")
nodes = json.loads(output("k3s", "kubectl", "get", "nodes", "-o", "json"))["items"]
require(len(nodes) == 1, "lite node validation supports one IPv4 node")
cidrs = [ipaddress.ip_network(cidr) for cidr in nodes[0]["spec"].get("podCIDRs", [])]
require(
    cidrs and all(cidr.version == 4 and cidr.subnet_of(ipaddress.ip_network("10.42.0.0/16")) for cidr in cidrs),
    "the node ICMP rule supports only the configured IPv4 Pod CIDR; revalidate changed or dual-stack networks",
)
subprocess.run(["iptables", "-t", "raw", "-C", "PREROUTING", "-s", "10.42.0.0/16", "-p", "icmp", "-j", "DROP"], check=True, timeout=10)
print("lite node runtime, disk cap, IPv4 Pod CIDR and ICMP rule verified")
