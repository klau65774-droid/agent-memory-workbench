"""The console evaluates microsecond validity intervals across writes, sleep and recall."""

from agent_memory.core import timestamp
from agent_memory.core.clock import FrozenClock
from agent_memory.core.recall import Recall
from agent_memory.server.main import create_app
from fastapi.testclient import TestClient


def test_scheduled_cutover_uses_the_same_half_open_interval_throughout_the_api(tmp_path):
    app = create_app(tmp_path / "demo")
    clock = FrozenClock(timestamp.parse("2030-05-15T07:00:00.000004Z"))
    first = "chronometer-before"
    second = "chronometer-after"
    with TestClient(app) as client:
        store = app.state.stores["default"]
        store.clock = clock
        for name, options in (
            (first, {"valid_from": "2030-05-15T15:00:00.000002+08:00"}),
            (second, {"valid_from": "2030-05-15T07:00:00.000005Z", "supersedes": first}),
        ):
            response = client.post(
                "/v1/memory/record",
                json={
                    "name": name, "type": "fact", "abstract": "Chronometer calibration", **options
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["written"]
        slept = client.post("/v1/memory/manage/sleep", json={"dry_run": False})
        assert slept.status_code == 200, slept.text
        previous = client.get("/v1/memory/records/" + first).json()["record"]
        current = client.get("/v1/memory/records/" + second).json()["record"]
        assert previous["valid_from"] == "2030-05-15T07:00:00.000002Z"
        assert previous["invalid_at"] == current["valid_from"] == "2030-05-15T07:00:00.000005Z"

        for instant, expected in (
            ("2030-05-15T07:00:00.000001Z", []),
            ("2030-05-15T07:00:00.000002Z", [first]),
            (None, [first]),
            ("2030-05-15T15:00:00.000005+08:00", [second]),
        ):
            response = client.post(
                "/v1/memory/recall", json={"query": "chronometer", "as_of": instant}
            )
            assert response.status_code == 200, response.text
            assert [hit["name"] for hit in response.json()["hits"]] == expected

        # Native current-state recall keeps the upstream active-surface contract.
        assert [hit.name for hit in Recall(store).recall("chronometer", log=False)] == [second]
        clock.advance(microseconds=1)
        response = client.post("/v1/memory/recall", json={"query": "chronometer"})
        assert [hit["name"] for hit in response.json()["hits"]] == [second]
