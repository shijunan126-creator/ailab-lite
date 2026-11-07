"""Real ENOSPC on a tiny disposable Linux tmpfs, never on the host disk."""

import io
import json
import os
import subprocess
import tarfile
import uuid
from pathlib import Path

import pytest

IMAGE = os.environ.get("AILAB_TEST_STORAGE_DOCKER_IMAGE", "")


@pytest.mark.skipif(not IMAGE, reason="set AILAB_TEST_STORAGE_DOCKER_IMAGE for disposable Docker tmpfs")
def test_local_storage_cleans_partial_write_and_recovers_after_real_enospc():
    package = Path(__file__).resolve().parents[2] / "ailab_platform"
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        for name in ["storage.py", "ports/storage.py", "adapters/local_object_index.py"]:
            archive.add(package / name, arcname="ailab_platform/" + name)
    name = "ailab-document-enospc-" + uuid.uuid4().hex[:10]
    script = """
import errno,json,os,sys,tarfile,types
from pathlib import Path
tarfile.open(fileobj=sys.stdin.buffer,mode='r|').extractall('/probe/src',filter='data')
sys.path.insert(0,'/probe/src')
# This component does not use Settings. Supply only the configuration module;
# all storage/index code is the unmodified production source from the tar.
config=types.ModuleType('ailab_platform.config');config.settings=object()
sys.modules[config.__name__]=config
from ailab_platform.storage import LocalStorage
storage=LocalStorage(Path('/probe/data'))
filler=Path('/probe/filler')
with filler.open('wb',buffering=0) as out:
    while os.statvfs('/probe').f_bavail*os.statvfs('/probe').f_frsize>256*1024:
        out.write(b'x'*65536)
try:
    storage.put_stream('derived/large',iter([b'a'*65536]*16),max_bytes=1024**2)
except OSError as error:
    assert error.errno==errno.ENOSPC,error
else:
    raise AssertionError('bounded tmpfs did not produce ENOSPC')
assert not storage.exists('derived/large')
assert not list(Path('/probe/data/.metadata').glob('*.tmp'))
with storage.index.connection() as db:
    assert db.execute('SELECT count(*) FROM pending').fetchone()[0]==0
filler.unlink()
storage.put_bytes('derived/recovered',b'recovered')
assert storage.get_bytes('derived/recovered')==b'recovered'
storage.close()
print(json.dumps({'errno':'ENOSPC','partial_objects':0,'pending_writers':0,'recovery':'passed'}))
"""
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "128m",
                "--cpus",
                "0.5",
                "--pids-limit",
                "64",
                "--tmpfs",
                "/probe:rw,size=8m,mode=1777",
                IMAGE,
                "sleep",
                "120",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        result = subprocess.run(
            ["docker", "exec", "-i", name, "python", "-c", script],
            input=payload.getvalue(),
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout)["recovery"] == "passed"
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
