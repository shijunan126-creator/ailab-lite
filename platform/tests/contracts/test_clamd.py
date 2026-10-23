"""Opt-in real clamd contract. AILAB_TEST_CLAMD=tcp://host:3310 or unix:///path."""

import io
import os

import pytest

from ailab_platform.adapters.clamav import ClamAVScanner


@pytest.mark.skipif(not os.environ.get("AILAB_TEST_CLAMD"), reason="AILAB_TEST_CLAMD unset")
def test_real_clamd_clean_and_eicar():
    scanner = ClamAVScanner(os.environ["AILAB_TEST_CLAMD"], timeout=30)
    assert scanner.scan(io.BytesIO(b"normal harmless document")).verdict == "clean"
    eicar = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    assert scanner.scan(io.BytesIO(eicar)).verdict == "infected"
