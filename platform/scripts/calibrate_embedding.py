#!/usr/bin/env python3
"""Pick AILAB_MEMORY_EMBEDDING_MIN_SIMILARITY for an embedding model.

Cosine scores are model-specific: bge-base-zh puts related text at 0.4-0.6 and unrelated at 0.2-0.37, e5-style models put
everything above 0.8. The history search needs the floor between the two groups, so measure it on the model you deploy.

    python platform/scripts/calibrate_embedding.py --url http://10.0.0.8:8080/v1 --model BAAI/bge-m3
    python platform/scripts/calibrate_embedding.py --url ... --model ... --query-prefix "<instruction>" --pairs mine.json

``--pairs`` is a JSON list of {"query": ..., "turn": ..., "related": true|false} taken from your own conversations; the
built-in set is a small Chinese/English technical sample and only a starting point.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys

import httpx

TURNS = {
    "k8s": "沙箱方案：OpenSandbox 还是 agent-sandbox？我倾向 Kubernetes。决定用 Kubernetes，因为空闲沙箱必须销毁，且需要 gVisor 隔离。",
    "ppt": "帮我做一个关于季度销售复盘的PPT，要有图表。已生成 8 页 PPT，包含销售趋势图和区域对比。",
    "weekly": "以后周报先写结论再写风险，别写流水账。好的，周报会按结论、风险、下周计划来写。",
    "sql": "SQLite 迁移时外键引用的表不能 batch 重建，怎么办。改成新建表、拷贝数据、再重命名，并在事务内完成。",
    "docker": "docker save 导出的镜像包校验失败怎么处理。用 docker load 回读并比对 digest，失败就重新导出。",
    "trip": "下周去东京出差，帮我列个行李清单。护照、充电器、转换插头、换洗衣物。",
    "redis": "Redis 限流为什么会 fail-open。Redis 出错时插件放行，所以需要监控与告警。",
    "poem": "写一首关于秋天的诗。秋风起，落叶满山。",
}
RELATED = [
    ("容器编排那件事为什么这么定", "k8s"),
    ("为什么选 Kubernetes 来跑沙箱", "k8s"),
    ("汇报文档的写法偏好", "weekly"),
    ("季度业绩演示稿", "ppt"),
    ("数据库表结构变更的坑", "sql"),
    ("镜像打包有没有问题", "docker"),
    ("出差要带什么", "trip"),
    ("rate limiter fails open when redis is down", "redis"),
]
UNRELATED = [
    ("今天天气怎么样", "k8s"),
    ("介绍一下量子计算", "ppt"),
    ("写一个排序算法", "weekly"),
    ("推荐一部电影", "sql"),
    ("怎么煮米饭", "docker"),
    ("what is the capital of France", "trip"),
    ("帮我翻译这句话", "redis"),
    ("今天天气怎么样", "poem"),
]


def embed(url: str, model: str, key: str, texts: list[str]) -> list[list[float]]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    out: list[list[float]] = []
    for i in range(0, len(texts), 16):
        r = httpx.post(
            url.rstrip("/") + "/embeddings",
            json={"model": model, "input": texts[i : i + 16]},
            headers=headers,
            timeout=120,
            trust_env=False,
        )
        r.raise_for_status()
        out += [row["embedding"] for row in sorted(r.json()["data"], key=lambda row: row["index"])]
    return out


def cosine(a: list[float], b: list[float]) -> float:
    return math.sumprod(a, b) / (math.sqrt(math.sumprod(a, a)) * math.sqrt(math.sumprod(b, b)) or 1.0)


def suggest(related: list[float], unrelated: list[float]) -> dict:
    """The floor that keeps the most related pairs without admitting an unrelated one, and how clean the split is."""
    top_noise, low_signal = max(unrelated), min(related)
    clean = low_signal > top_noise
    # with an overlap, stay just above the noise: a missed match costs a lexical fallback, a false one costs a wrong answer
    floor = (top_noise + low_signal) / 2 if clean else top_noise + 0.01
    kept = sum(1 for s in related if s >= floor)
    return {
        "suggested_min_similarity": round(floor, 2),
        "clean_split": clean,
        "related_kept": f"{kept}/{len(related)}",
        "related": {"min": round(low_signal, 3), "median": round(statistics.median(related), 3)},
        "unrelated": {"max": round(top_noise, 3), "median": round(statistics.median(unrelated), 3)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="the part before /embeddings")
    ap.add_argument("--model", required=True)
    ap.add_argument("--key", default="")
    ap.add_argument("--query-prefix", default="")
    ap.add_argument("--doc-prefix", default="")
    ap.add_argument("--pairs", help="JSON file of {query, turn, related} from your own conversations")
    args = ap.parse_args()

    if args.pairs:
        rows = json.load(open(args.pairs, encoding="utf-8"))
        pairs = [(r["query"], r["turn"], bool(r["related"])) for r in rows]
    else:
        pairs = [(q, TURNS[t], True) for q, t in RELATED] + [(q, TURNS[t], False) for q, t in UNRELATED]
    queries = [args.query_prefix + q for q, _, _ in pairs]
    turns = [args.doc_prefix + t for _, t, _ in pairs]
    qv, tv = embed(args.url, args.model, args.key, queries), embed(args.url, args.model, args.key, turns)
    scores = [cosine(a, b) for a, b in zip(qv, tv, strict=True)]
    related = [s for s, (_, _, rel) in zip(scores, pairs, strict=True) if rel]
    unrelated = [s for s, (_, _, rel) in zip(scores, pairs, strict=True) if not rel]
    if not related or not unrelated:
        print("need both related and unrelated pairs", file=sys.stderr)
        return 2
    result = suggest(related, unrelated)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["clean_split"]:
        print("\nThe groups overlap: this model (or prefix) does not separate them well; lexical search will carry more.", file=sys.stderr)
    print(f"\nAILAB_MEMORY_EMBEDDING_MIN_SIMILARITY={result['suggested_min_similarity']}   (deploy.sh --embedding-min-similarity)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
