"""Template upload→outbox→Worker→API→Agent→Broker, then clean restored workspace."""

import io
import time

import httpx
import pytest
from pptx import Presentation

from .conftest import PASSWORD, USERNAME

pytestmark = pytest.mark.e2e


def wait(client, path, predicate, seconds=60):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        value = response.json()
        if predicate(value):
            return value
        time.sleep(0.2)
    raise AssertionError(f"condition not reached: {path}; last={value}")


def test_template_upload_run_and_next_turn_reset(stack):
    with httpx.Client(base_url=stack.api, timeout=15) as client:
        login = client.post("/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
        assert login.status_code == 200, login.text
        client.headers["Authorization"] = "Bearer " + login.json()["token"]
        stream = io.BytesIO()
        prs = Presentation()
        prs.slides.add_slide(prs.slide_layouts[0]).shapes.title.text = "Template sample"
        prs.save(stream)
        response = client.post("/v1/templates", files={"upload": ("company.pptx", stream.getvalue())}, data={"scope": "company"})
        assert response.status_code == 201, response.text
        template_id = response.json()["id"]
        try:
            tpl = wait(client, f"/v1/templates/{template_id}", lambda row: row["status"] != "pending")
            assert tpl["status"] == "ready", tpl
            thumb = client.get(tpl["thumbnail_url"])
            assert thumb.status_code == 200 and "immutable" in thumb.headers["cache-control"]
            conv = client.post("/v1/conversations", json={}).json()
            path = f"/v1/conversations/{conv['id']}/messages"
            first = client.post(path, json={"prompt": "PPT模板链路-有模板", "template": {"id": template_id}})
            assert first.status_code == 201, first.text
            run = wait(client, "/v1/runs/" + first.json()["id"], lambda r: r["status"] in {"succeeded", "failed"})
            assert run["status"] == "succeeded" and "TEMPLATE_PRESENT" in run["summary"], run
            assert run["template"] == {"source": "template", "name": "company"}
            second = client.post(path, json={"prompt": "PPT模板链路-无模板"})
            assert second.status_code == 201, second.text
            plain = wait(client, "/v1/runs/" + second.json()["id"], lambda r: r["status"] in {"succeeded", "failed"})
            assert plain["status"] == "succeeded" and "TEMPLATE_ABSENT" in plain["summary"], plain
            assert plain["template"] is None
            client.delete("/v1/conversations/" + conv["id"])
        finally:
            assert client.delete(f"/v1/templates/{template_id}").status_code == 204
