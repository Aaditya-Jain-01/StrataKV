import os
import shutil
from fastapi.testclient import TestClient
import api.main as app_module

E2E_STORE_DIR = "e2e_data_store"

def setup_module():
    if os.path.exists(E2E_STORE_DIR):
        shutil.rmtree(E2E_STORE_DIR, ignore_errors=True)
    app_module.DATA_DIRECTORY = E2E_STORE_DIR

def teardown_module():
    if os.path.exists(E2E_STORE_DIR):
        shutil.rmtree(E2E_STORE_DIR, ignore_errors=True)

def test_full_engine_lifecycle():
    with TestClient(app_module.app) as client:
        res = client.put("/api/v1/records/device:alpha", json={"value": "sensor_reading_100"})
        assert res.status_code == 200
        assert res.json()["status"] == "success"

        res = client.get("/api/v1/records/device:alpha")
        assert res.status_code == 200
        assert res.json()["value"] == "sensor_reading_100"

        for i in range(150):
            res = client.put(f"/api/v1/records/telemetry:{i:04d}", json={"value": f"packet_{i}_{'z'*300}"})
            assert res.status_code == 200

        metrics_res = client.get("/api/v1/engine/metrics")
        assert metrics_res.status_code == 200
        metrics = metrics_res.json()
        assert metrics["sstable_count"] >= 1

        res = client.get("/api/v1/records/device:alpha")
        assert res.status_code == 200
        assert res.json()["value"] == "sensor_reading_100"

        res = client.get("/api/v1/records/non_existent_key_9999")
        assert res.status_code == 404

        res = client.delete("/api/v1/records/device:alpha")
        assert res.status_code == 200
        assert res.json()["status"] == "deleted"

        res = client.get("/api/v1/records/device:alpha")
        assert res.status_code == 404

        res = client.put("/api/v1/records/unflushed:wal:key", json={"value": "must_survive"})
        assert res.status_code == 200

    with TestClient(app_module.app) as client_reloaded:
        res = client_reloaded.get("/api/v1/records/unflushed:wal:key")
        assert res.status_code == 200
        assert res.json()["value"] == "must_survive"

        res = client_reloaded.get("/api/v1/records/device:alpha")
        assert res.status_code == 404

        res = client_reloaded.post("/api/v1/engine/flush")
        assert res.status_code == 200

        pre_compact_tables = client_reloaded.get("/api/v1/engine/metrics").json()["sstable_count"]
        assert pre_compact_tables >= 2

        res = client_reloaded.post("/api/v1/engine/compact")
        assert res.status_code == 200

        post_metrics = client_reloaded.get("/api/v1/engine/metrics").json()
        assert post_metrics["sstable_count"] == 1

        res = client_reloaded.get("/api/v1/records/telemetry:0042")
        assert res.status_code == 200
        assert "packet_42" in res.json()["value"]

if __name__ == "__main__":
    setup_module()
    test_full_engine_lifecycle()
    teardown_module()
    print("=" * 65)
    print(" [STATUS] ALL END-TO-END TESTS PASSED CLEANLY")
    print("=" * 65)