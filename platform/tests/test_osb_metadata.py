"""OpenSandbox metadata must survive the server's Kubernetes label constraints."""

import re

from ailab_platform.sandbox.opensandbox import _decode_metadata, _encode_metadata


def test_metadata_codec_preserves_lease_fields_and_label_filter():
    labels = {
        "ailab.owner": "single-node",
        "ailab.created_at": "2026-10-23T06:23:53.007859+00:00",
        "ailab.spec": '{"image":"registry/office:lite","memory":805306368}',
        "ailab.injected": '{"/workspace/inputs/中文.txt":"' + "a" * 256 + '"}',
        "ailab.empty": "",
        "ailab.marker": "b32.2",
    }
    encoded = _encode_metadata(labels)
    assert all(len(v) <= 63 and re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9_.-]*[A-Za-z0-9])?", v) for v in encoded.values())
    assert _decode_metadata(encoded) == labels
    assert _encode_metadata({"ailab.owner": "single-node"}).items() <= encoded.items()
    # Metadata PATCH merges keys. A shorter replacement must ignore old chunk tails.
    updated = {**encoded, **_encode_metadata({"ailab.injected": "{}", "ailab.empty": "now-present"})}
    assert _decode_metadata(updated) == {**labels, "ailab.injected": "{}", "ailab.empty": "now-present"}
