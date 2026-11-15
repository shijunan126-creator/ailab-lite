"""The web tool's private search dependency must survive deployment refactors."""

from pathlib import Path
from urllib.parse import urlsplit

import yaml


def _resources(directory: Path):
    manifest = yaml.safe_load((directory / "kustomization.yaml").read_text())
    for name in manifest["resources"]:
        path = directory / name
        if path.is_dir():
            yield from _resources(path)
        else:
            yield from (doc for doc in yaml.safe_load_all(path.read_text()) if doc)


def test_agent_search_reaches_a_private_service_through_the_base_policy():
    base = Path(__file__).resolve().parents[1] / "deploy/base"
    documents = {(doc["kind"], doc["metadata"]["name"]): doc for doc in _resources(base)}
    config = documents["ConfigMap", "platform-config"]["data"]
    assert config["AILAB_WEB_SEARCH_PROVIDER"] == "searxng"
    url = urlsplit(config["AILAB_SEARXNG_URL"])
    service = documents["Service", url.hostname]
    assert service["spec"].get("type", "ClusterIP") == "ClusterIP"
    assert service["spec"]["ports"][0]["port"] == url.port
    deployment = documents["Deployment", url.hostname]
    pod = deployment["spec"]["template"]
    assert service["spec"]["selector"] == pod["metadata"]["labels"]
    assert all(not container.get("envFrom") for container in pod["spec"]["containers"])
    policy = documents["NetworkPolicy", url.hostname]["spec"]
    assert policy["podSelector"]["matchLabels"] == pod["metadata"]["labels"]
    assert policy["ingress"] == [
        {"from": [{"podSelector": {"matchLabels": {"app": "platform-agent"}}}], "ports": [{"protocol": "TCP", "port": url.port}]}
    ]


def test_no_browser_renderer_or_its_egress_proxy_is_deployed():
    base = Path(__file__).resolve().parents[1] / "deploy/base"
    documents = {(doc["kind"], doc["metadata"]["name"]): doc for doc in _resources(base)}
    config = documents["ConfigMap", "platform-config"]["data"]
    assert "AILAB_CRAWLER_URL" not in config
    assert not any(name in {"platform-crawler", "platform-egress"} for _, name in documents)
    for service in ("platform-agent", "platform-api"):
        pod = documents["Deployment", service]["spec"]["template"]["spec"]
        assert not any("crawler" in str(entry) for entry in pod["containers"][0].get("envFrom", []))
