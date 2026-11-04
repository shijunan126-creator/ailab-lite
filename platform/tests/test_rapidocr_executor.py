"""Resident OCR configuration without installing an inference engine in the API."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("backend", ["cpu", "cann"])
@pytest.mark.parametrize("profile_prefix", [None, "", "/tmp/diagnostic-profile"])
def test_node_profiling_requires_explicit_diagnostic_prefix(monkeypatch, backend, profile_prefix):
    spec = importlib.util.spec_from_file_location("rapidocr_executor", Path(__file__).parents[2] / "images/rapidocr/executor.py")
    executor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(executor)
    if profile_prefix is None:
        monkeypatch.delenv("OCR_PROFILE_PREFIX", raising=False)
    else:
        monkeypatch.setenv("OCR_PROFILE_PREFIX", profile_prefix)
    entries = {}
    opts = SimpleNamespace(enable_profiling=True, add_session_config_entry=lambda key, value: entries.update({key: value}))
    assert executor.configure_session_options(opts, backend) is opts
    assert opts.enable_profiling is bool(profile_prefix)
    assert getattr(opts, "profile_file_prefix", None) == (profile_prefix or None)
    assert entries == ({"session.disable_cpu_ep_fallback": "1"} if backend == "cann" else {})
