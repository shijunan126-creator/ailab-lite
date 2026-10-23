"""Sandbox skill attestation: ask the leased sandbox itself, never infer its contents from an image tag."""

import json
import logging
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Query

from ..config import settings
from ..sandbox import SandboxError
from ..sandbox.mock import SKILLS as MOCK_SKILLS
from ..service_auth import require_scope
from ..skill_index import checked_index
from .service import LeaseError, LeaseLostError

router = APIRouter()
log = logging.getLogger("ailab.broker")

# Runs inside the image. Exit 3 means index drift, 2 an invalid manifest or missing requirement, 0 the index it computed.
VERIFY_COMMAND = "python /opt/ailab-tools/skill_index.py /opt/skills --check --requirements"
MAX_ATTESTATIONS = 512

# (lease_id, provider_ref) -> verified result. The image's /opt/skills is not writable by the runtime user, so one
# successful exec per resource is enough. A released lease fails in manager.get(); a replaced resource has a new key.
_attested: OrderedDict[tuple[str, str], dict] = OrderedDict()


def verify_lease_index(manager, lease_id: str) -> dict:
    lease = manager.get(lease_id)
    if manager.provider.name == "mock":
        # The mock serves /opt/skills from the host checkout (sandbox/mock.py), so there is no image to attest: check
        # the directory it actually serves, which still catches a checkout whose files drifted from its index.
        # The secure profile refuses the mock provider, so this never stands in for an image attestation.
        try:
            index = checked_index(MOCK_SKILLS)
        except (ValueError, OSError) as exc:
            raise HTTPException(409, "skill_index_mismatch") from exc
        return _checked_result(_HostIndex(index), lease_id, lease["provider_ref"], "host-checkout")
    key = (lease_id, lease["provider_ref"])
    if key not in _attested:
        result = manager.exec(lease_id, VERIFY_COMMAND, 30)
        _attested[key] = _checked_result(result, lease_id, lease["provider_ref"], lease["image"])
        while len(_attested) > MAX_ATTESTATIONS:
            _attested.popitem(last=False)
    return _attested[key]


class _HostIndex:
    """The shape of a verifier exec result, for the index the mock computed on the host."""

    def __init__(self, index: dict):
        self.exit_code, self.stdout = 0, json.dumps(index)


def _checked_result(result, lease_id: str, provider_ref: str, image_ref: str) -> dict:
    if result.exit_code == 3:
        log.error("skill_index_mismatch in leased sandbox")
        raise HTTPException(409, "skill_index_mismatch")
    if result.exit_code != 0:
        log.error("sandbox skill verifier exited %s", result.exit_code)
        raise HTTPException(503, "skill_verification_failed")
    try:
        index = json.loads(result.stdout)
    except ValueError as exc:
        raise HTTPException(503, "skill_verification_failed") from exc
    try:
        expected = checked_index(settings.skills_path)
    except (ValueError, OSError) as exc:
        # The Broker's own source tree is broken; that is a deploy error, not evidence about the sandbox.
        log.error("broker skill source is inconsistent")
        raise HTTPException(503, "skill_index_unverified") from exc
    if index != expected:
        log.error("skill_index_mismatch in leased sandbox")
        raise HTTPException(409, "skill_index_mismatch")
    return {"verified": True, "lease_id": lease_id, "provider_ref": provider_ref, "image_ref": image_ref, "index": index}


@router.get("/internal/skills/index", dependencies=[Depends(require_scope("sandbox:use"))])
def sandbox_index(lease_id: str = Query(default="", max_length=64)):
    if not lease_id:
        raise HTTPException(422, "lease_id is required")
    from .main import get_manager  # imported late: main registers this router while it is being built

    try:
        return verify_lease_index(get_manager(), lease_id)
    except LeaseError as exc:
        raise HTTPException(410 if isinstance(exc, LeaseLostError) else 503, exc.detail()) from exc
    except SandboxError as exc:
        raise HTTPException(502, "sandbox skill verification unavailable") from exc
