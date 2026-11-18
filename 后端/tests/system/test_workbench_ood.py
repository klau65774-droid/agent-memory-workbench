"""Fixed workbench holdout. Run this file with --output to save one HTTP observation per case."""

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

import pytest
from agent_memory.core.config import Config
from agent_memory.core.database import Database
from agent_memory.core.poison_guard import inspect_memory_safety
from agent_memory.core.store import Store
from agent_memory.core.tokenization import TOKENIZER_HYBRID
from agent_memory.server.main import create_app
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parents[1] / "fixtures" / "workbench"
SUITES = ("ood", "ood_b", "ood_c")
RECORD = "/v1/memory/record"
RECALL = "/v1/memory/recall"
TECH = {"X-Namespace": "finance-agent"}


def _indexed(store, name):
    with Database(store.layout).connect() as connection:
        return {
            surface: [
                tuple(row)
                for row in connection.execute(f"SELECT * FROM {surface} WHERE name = ?", (name,))
            ]
            for surface in ("records", "chunks", "history")
        }


def _timed_post(client, path, payload, headers):
    start = time.perf_counter_ns()
    response = client.post(path, json=payload, headers=headers)
    elapsed = (time.perf_counter_ns() - start) / 1_000_000
    return response.json(), {
        "http_status": response.status_code,
        "wall_ms": round(elapsed, 3),
        "server_ms": float(response.headers["X-Memory-Latency-Ms"]),
    }


def _one_boundary(wrapped, body):
    start = wrapped.find("<untrusted_memory_data ")
    if start < 0:
        return False
    try:
        root = ElementTree.fromstring(wrapped[start:])
    except ElementTree.ParseError:
        return False
    return (
        root.tag == "untrusted_memory_data"
        and root.attrib == {"trust": "quarantined"}
        and not len(root)
        and body in (root.text or "")
    )


def _observe(client, store, case):
    group = case["group"]
    if group == "retrieval":
        data, timing = _timed_post(client, RECALL, {"query": case["query"], "limit": 3}, TECH)
        if timing["http_status"] != 200:
            return False, timing, {"error": data.get("detail")}
        hits = [hit["name"] for hit in data["hits"]]
        target = case["target"]
        return (
            target in hits,
            timing,
            {"hits": hits, "rank": hits.index(target) + 1 if target in hits else None},
        )

    name = "ood-" + case["id"].lower()
    body = case["body"]
    label = "papercard" + case["id"]
    spec = {"type": "fact", "name": name, "abstract": label, "body": body}
    excerpt = " ".join(body.splitlines())
    if group == "markup":
        # Abstract is a single frontmatter line. The complete original remains in the body.
        spec["abstract"] = label + " " + excerpt
    data, timing = _timed_post(client, RECORD, spec, {"X-Namespace": "default"})
    if timing["http_status"] != 200:
        return False, timing, {"error": data.get("detail")}
    saved = store.find(name)
    index = _indexed(store, name)
    info = {
        "written": data["written"],
        "trust": data["trust_level"],
        "record_on_disk": saved is not None,
        "categories": data["redactions_applied"],
    }
    if group.startswith("command_") or group == "markup":
        passed = (
            data["quarantined"]
            and not data["written"]
            and saved is None
            and not any(index.values())
        )
        if group == "markup":
            retained = client.post(RECORD, json={**spec, "quarantine_on_poison": False})
            retained.raise_for_status()
            recalled = client.post(
                RECALL, json={"query": label, "limit": 1, "include_quarantined": True}
            )
            recalled.raise_for_status()
            result = recalled.json()
            info["contained"] = bool(
                retained.json()["written"]
                and result["hits"]
                and result["hits"][0]["name"] == name
                and _one_boundary(result["hits"][0]["abstract"], excerpt)
                and _one_boundary(result["context_preview"], excerpt)
                and _one_boundary(inspect_memory_safety("", body).spotlit_text, body)
            )
            passed = passed and info["contained"]
    else:
        expected = case.get("clean", body)
        categories = case.get("categories", [])
        passed = (
            data["written"]
            and data["trust_level"] == "trusted"
            and sorted(data["redactions_applied"]) == sorted(set(categories))
            and data["record"]["redacted_count"] == len(categories)
            and data["redacted_body_preview"] == expected
            and saved is not None
            and saved.body.strip() == expected
            and bool(index["records"])
            and bool(index["chunks"])
        )
        if group == "redaction":
            # Derive synthetic credential values only from the frozen gold input; inspect FTS.
            secrets = [
                word for word in body.split() if word.startswith(("ghp_", "github_", "AKIA"))
            ]
            passed = passed and all(value not in str(index) for value in secrets)
    return bool(passed), timing, info


def run_suite(root, fixture):
    # Seed a separate namespace with technical memories and distractors. No demo answer
    # documents or safety evaluation writes enter the retrieval corpus.
    suite = json.loads(fixture.read_text(encoding="utf-8"))
    config = Config.default()
    config.index.tokenizer = TOKENIZER_HYBRID
    config.index.use_jieba = False
    tech = Store(root / "finance-agent", config)
    tech.init()
    setup = tech.record_many(
        [
            {"name": name, "type": "fact", "abstract": title, "body": body}
            for name, title, body in suite["documents"]
        ]
    )
    assert not setup.rejected
    observations = []
    with TestClient(create_app(root)) as client:
        store = client.app.state.stores["default"]
        for case in suite["cases"]:
            passed, timing, info = _observe(client, store, case)
            observations.append(
                {"id": case["id"], "group": case["group"], "passed": passed, **timing, **info}
            )
    times = sorted(row["wall_ms"] for row in observations)
    return {
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "mean_wall_ms": round(statistics.mean(times), 3),
        "p95_wall_ms": times[math.ceil(0.95 * len(times)) - 1],
        "cases": observations,
    }


@pytest.fixture(scope="module")
def observations(tmp_path_factory):
    rows = {}
    for name in SUITES:
        result = run_suite(tmp_path_factory.mktemp(name), FIXTURES / (name + ".json"))
        rows.update((row["id"], row) for row in result["cases"])
    return rows


def pytest_generate_tests(metafunc):
    if "case_id" in metafunc.fixturenames:
        ids = [
            row["id"]
            for name in SUITES
            for row in json.loads((FIXTURES / (name + ".json")).read_text("utf-8"))["cases"]
        ]
        assert len(set(ids)) == len(ids)
        metafunc.parametrize("case_id", ids)


def test_frozen_case(case_id, observations):
    row = observations[case_id]
    assert row["passed"], row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--suite", choices=SUITES, default="ood")
    options = parser.parse_args()
    with TemporaryDirectory(prefix="am-ood-") as directory:
        report = run_suite(Path(directory), FIXTURES / (options.suite + ".json"))
    options.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: val for key, val in report.items() if key != "cases"}))
    for row in report["cases"]:
        print(row["id"], row["group"], row["passed"], row["wall_ms"])
    raise SystemExit(int(any(not row["passed"] for row in report["cases"])))
