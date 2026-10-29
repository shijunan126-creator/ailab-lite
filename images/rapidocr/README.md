# RapidOCR executor

Dedicated OCR host only; do not install these models in user sandboxes. Business services retain their existing boundaries.

Source pin: RapidAI/RapidOCR `80b427d69bfc5b91a12913394875910296c6f315` (Apache-2.0). Install that source and the target-specific, pinned ONNX Runtime CANN wheel in the approved Ascend environment; this repository does not invent a universal driver/CANN/ORT combination. Run `uvicorn executor:app --workers 1`; enforce process/container memory, CPU, NPU allocation and request deadline at the host gateway.

`OCR_MODEL_MANIFEST` must contain `models.Det/Cls/Rec` with absolute `path`, `sha256`, source URL and model license. Weights are pre-provisioned: no runtime model download is permitted. `OCR_REQUIRED_BACKEND=cann` is the default; `cpu` requires an explicit setting. `OCR_EXECUTOR_TOKEN` is a service secret. Platform settings are `AILAB_OCR_EXECUTOR_URL`, `AILAB_OCR_EXECUTOR_TOKEN`, and `AILAB_OCR_REQUIRED_BACKEND`, and `AILAB_OCR_ENGINE_FINGERPRINT` (matching the executor readiness response).

Readiness checks all three actual sessions, not just installed providers. This is necessary but insufficient proof of NPU execution: acceptance must additionally retain ORT node profiling, CANN/driver/wheel/model identities, NPU utilization and cold/hot/30-minute measurements on the real host. No such hardware evidence exists in this local change. CPU preprocessing or permitted CPU operators must be distinguished from full-model fallback.

OCR records pages, engine/backend and elapsed seconds; it does not bill tokens/points or impose per-user usage quotas. Request byte/pixel/deadline and executor concurrency limits remain fault-isolation boundaries.
