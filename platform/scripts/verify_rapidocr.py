"""Opt-in synthetic-image check through the real OCR executor, API, Worker and private cache."""

import argparse
import io
import json
import os
from pathlib import Path

import httpx


def main(args):
    args.state_dir.mkdir(parents=True, exist_ok=True)
    ready = httpx.get(args.executor + "/ready", timeout=10).json()
    if ready.get("backend") != "cpu":
        raise RuntimeError("this development check requires explicitly configured CPU executor")
    os.environ.update(
        AILAB_ENV="test",
        AILAB_PROFILE="test",
        AILAB_ENV_FILE=str(args.state_dir / "no.env"),
        AILAB_DATA_DIR=str(args.state_dir),
        AILAB_STORAGE_ROOT=str(args.state_dir / "objects"),
        AILAB_DATABASE_URL="sqlite:///" + str(args.state_dir / "verification.db"),
        AILAB_QUEUE_BACKEND="memory",
        AILAB_DEV_TOKEN="ocr-check-token",
        AILAB_SERVICE_TOKEN="ocr-check-service",
        AILAB_MODELS_FILE=str(Path(__file__).resolve().parents[1] / "tests/models.test.toml"),
        AILAB_OCR_EXECUTOR_URL=args.executor,
        AILAB_OCR_EXECUTOR_TOKEN=args.token_file.read_text().strip(),
        AILAB_OCR_ENGINE_FINGERPRINT=ready["fingerprint"],
        AILAB_OCR_REQUIRED_BACKEND="cpu",
    )
    from fastapi.testclient import TestClient
    from PIL import Image, ImageDraw, ImageFont
    from sqlalchemy import select

    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import ModelUsage, OcrPageResult
    from ailab_platform.worker.ocr import process_ocr
    from ailab_platform.worker.pipeline import process_file

    auth, service = {"Authorization": "Bearer ocr-check-token"}, {"X-Service-Token": "ocr-check-service"}
    image = Image.new("RGB", (1200, 350), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(args.font, 48)
    for index, line in enumerate(["合同编号 AL-219", "金额 12345.67 元", "截止日期 2031-06-18"]):
        draw.text((50, 35 + index * 95), line, font=font, fill="black")
    encoded = io.BytesIO()
    image.save(encoded, "PNG")
    with TestClient(app) as client:

        def request(method, path, body=None, params=None):
            r = client.request(method, path, headers=service, json=body, params=params)
            if r.status_code >= 400:
                raise RuntimeError(f"verification API {r.status_code}: {r.text[:160]}")
            return r.json()

        class Bridge:
            def get_file(self, fid, **params):
                return request("GET", f"/internal/files/{fid}", params=params)

            def download_file(self, fid, path, **params):
                params.pop("max_bytes", None)
                response = client.get(f"/internal/files/{fid}/content", headers=service, params=params)
                response.raise_for_status()
                path.write_bytes(response.content)

            def patch_file(self, fid, **body):
                return request("PATCH", f"/internal/files/{fid}", body, {"job": "processing"})

            def ocr(self, fid, prepare=False):
                return request("POST", f"/internal/files/{fid}/ocr" + ("/prepare" if prepare else ""))

            def reuse_document(self, fid, scan):
                return request("POST", f"/internal/files/{fid}/reuse-document", {"scan": scan})

            def document_cache_pages(self, fid, source_id, after=-1):
                return request("GET", f"/internal/files/{fid}/document-cache-pages", params={"source_id": source_id, "after": after})

            def publish_cache_pages(self, fid, body):
                return request("POST", f"/internal/files/{fid}/document-cache-pages", body)

        bridge = Bridge()

        def upload():
            r = client.post("/v1/files", headers=auth, files={"upload": ("ocr-core.png", encoded.getvalue(), "image/png")})
            r.raise_for_status()
            fid = r.json()["id"]
            process_file(fid, platform=bridge)
            return fid

        first = upload()
        requested = client.post(f"/v1/files/{first}/extract-text", headers=auth, json={})
        requested.raise_for_status()
        assert process_ocr(first, bridge)["ocr"] == "done"
        text = client.get(f"/v1/files/{first}/extracted-text", headers=auth).text
        assert all(term in text.replace(" ", "") for term in ("AL-219", "12345.67", "2031-06-18")), text
        second = upload()
        copied = client.get(f"/v1/files/{second}/extracted-text", headers=auth)
        assert copied.status_code == 200 and copied.text == text
        with SessionLocal() as db:
            old, new = (
                db.scalar(select(OcrPageResult).where(OcrPageResult.file_id == first)),
                db.scalar(select(OcrPageResult).where(OcrPageResult.file_id == second)),
            )
            assert old and new and old.text_key != new.text_key and new.seconds == 0
            assert list(db.scalars(select(ModelUsage))) == []
            result = {
                "backend": "cpu",
                "providers": ready["providers"],
                "fingerprint": ready["fingerprint"],
                "engine": old.engine,
                "inference_seconds": old.seconds,
                "actual_pages": 1,
                "copied_pages": 1,
                "copied_inference_seconds": new.seconds,
                "model_calls": 0,
                "text": text,
                "checks": {
                    "identifier_amount_date_exact": True,
                    "duplicate_upload_private_ocr_cache": True,
                    "cache_has_no_second_inference": True,
                },
                "scope": "one synthetic Chinese image; not NPU or corpus accuracy",
            }
        assert client.delete(f"/v1/files/{first}", headers=auth).status_code == 204
        assert client.get(f"/v1/files/{second}/extracted-text", headers=auth).text == text
        result["checks"]["deleting_original_preserves_private_copy"] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--font", required=True)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args())
