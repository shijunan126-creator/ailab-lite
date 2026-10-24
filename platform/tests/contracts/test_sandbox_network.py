"""Opt-in deny-all probes against known live targets on a disposable cluster."""

import os
import shlex
import uuid

import pytest

from ailab_platform.config import settings
from ailab_platform.sandbox.base import SandboxSpec
from tests.contracts.test_sandbox_lifecycle import provider  # noqa: F401


def test_sandbox_cannot_initiate_tcp_udp_or_icmp(provider):  # noqa: F811
    targets = os.environ.get("AILAB_TEST_SANDBOX_TCP_TARGETS", "")
    udp = os.environ.get("AILAB_TEST_SANDBOX_UDP_TARGET", "")
    if not targets or not udp or provider.name == "mock":
        pytest.skip("set TCP_TARGETS and UDP_TARGET to known live disposable endpoints")
    spec = SandboxSpec(image=settings.sandbox_image, ttl=120)
    ref = provider.acquire("network-" + uuid.uuid4().hex[:12], spec.image, spec=spec)
    # TCP includes public, Service, Pod, node and metadata. UDP must be an echo
    # responder/DNS endpoint, with reachability independently checked from node.
    code = f"""
import socket, struct
for target in {targets.split(",")!r}:
    host, port = target.rsplit(':', 1)
    try:
        s = socket.create_connection((host, int(port)), 1)
    except OSError:
        continue
    s.close()
    raise AssertionError('TCP egress: ' + target)
host, port = {udp!r}.rsplit(':', 1)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(1)
try:
    s.connect((host, int(port)))
    s.send(b'network-contract')
    s.recv(1024)
except OSError:
    pass
else:
    raise AssertionError('UDP egress')
s.close()
# A ping socket is available even without CAP_NET_RAW in gVisor. Its outbound
# packets need the node rule; dropping capabilities alone is insufficient.
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
except OSError:
    pass
else:
    s.settimeout(1)
    payload = struct.pack('!BBHHH', 8, 0, 0xf7fe, 0, 1)
    try:
        s.sendto(payload, ({targets.split(",")[-2].rsplit(":", 1)[0]!r}, 0))
        s.recv(1024)
    except OSError:
        pass
    else:
        raise AssertionError('ICMP egress')
    s.close()
print('TCP UDP ICMP blocked')
"""
    try:
        result = provider.exec(ref, "python -c " + shlex.quote(code), 20)
        assert result.exit_code == 0, result.stderr
        assert "TCP UDP ICMP blocked" in result.stdout
    finally:
        provider.release(ref)
