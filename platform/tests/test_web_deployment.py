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


def test_crawler_is_reachable_only_from_the_agent_and_leaves_only_through_the_egress_proxy():
    base = Path(__file__).resolve().parents[1] / "deploy/base"
    documents = {(doc["kind"], doc["metadata"]["name"]): doc for doc in _resources(base)}
    config = documents["ConfigMap", "platform-config"]["data"]
    url = urlsplit(config["AILAB_CRAWLER_URL"])
    assert documents["Service", url.hostname]["spec"]["ports"][0]["port"] == url.port
    pod = documents["Deployment", url.hostname]["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    (crawler,) = pod["containers"]
    # Only its own token: no platform config, identities or model keys reach the browser.
    assert not crawler.get("envFrom")
    assert {e["valueFrom"]["secretKeyRef"]["name"] for e in crawler["env"] if "valueFrom" in e} == {"platform-crawler-token"}
    assert crawler["resources"]["limits"]["memory"] == "1Gi"

    policy = documents["NetworkPolicy", "platform-crawler"]["spec"]
    assert set(policy["policyTypes"]) == {"Ingress", "Egress"}
    assert policy["ingress"] == [
        {"from": [{"podSelector": {"matchLabels": {"app": "platform-agent"}}}], "ports": [{"protocol": "TCP", "port": 8090}]}
    ]
    assert policy["egress"] == [
        {"to": [{"podSelector": {"matchLabels": {"app": "platform-egress"}}}], "ports": [{"protocol": "TCP", "port": 4750}]},
        {
            "to": [
                {
                    "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}},
                    "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
                }
            ],
            "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
        },
    ]

    egress = documents["NetworkPolicy", "platform-egress"]["spec"]
    assert egress["ingress"][0]["from"] == [{"podSelector": {"matchLabels": {"app": "platform-crawler"}}}]
    public = next(rule for rule in egress["egress"] if "ipBlock" in rule["to"][0])
    assert {"10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8", "100.64.0.0/10"} <= set(
        public["to"][0]["ipBlock"]["except"]
    )
    assert {p["port"] for p in public["ports"]} == {80, 443}
    proxy = documents["Deployment", "platform-egress"]["spec"]["template"]["spec"]["containers"][0]
    assert proxy["command"] == ["python", "-m", "ailab_platform.egress"] and not proxy.get("envFrom")

    agent = documents["Deployment", "platform-agent"]["spec"]["template"]["spec"]["containers"][0]
    assert {"secretRef": {"name": "platform-crawler-token"}} in agent["envFrom"]
