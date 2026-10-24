"""Bounded readiness for disposable CI services; no access to developer settings."""

import argparse
import socket
import time
import urllib.error
import urllib.request


def ready(component):
    if component == "clamd":
        with socket.create_connection(("127.0.0.1", 13310), timeout=2) as stream:
            stream.settimeout(2)
            stream.sendall(b"zPING\0")
            return stream.recv(32).rstrip(b"\0\n") == b"PONG"
    # Do not use proxy environment variables for a loopback-only fixture.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:18080/realms/ci/.well-known/openid-configuration", timeout=2) as response:
        return response.status == 200


def wait(component, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if ready(component):
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    raise TimeoutError(f"{component} fixture did not become ready in {timeout}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", choices=("clamd", "keycloak"))
    wait(parser.parse_args().component)
