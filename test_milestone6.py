import os
import shutil
from fastapi.testclient import TestClient
import api.main as app_module

TEST_SERVICE_DIR = "test_api_store"

def cleanup():
    if os.path.exists(TEST_SERVICE_DIR):
        shutil.rmtree(TEST_SERVICE_DIR)

def run_milestone6_tests() -> None:
    print("-" * 65)
    print(" STRATAKV MILESTONE 6: ASYNC FASTAPI SERVICE VERIFICATION ")
    print("-" * 65)

    cleanup()
    app_module.DATA_DIRECTORY = TEST_SERVICE_DIR

    with TestClient(app_module.app) as client:
        print("[TEST 1] Testing PUT /records/{key} endpoint...")
        res = client.put("/api/v1/records/user:alpha", json={"value": "payload_data_alpha"})
        assert res.status_code == 200
        assert res.json() == {"status": "success", "key": "user:alpha"}
        print("  PUT request processed successfully.")

        print("\n[TEST 2] Testing GET /records/{key} point lookup...")
        res = client.get("/api/v1/records/user:alpha")
        assert res.status_code == 200
        assert res.json() == {"key": "user:alpha", "value": "payload_data_alpha"}

        res_missing = client.get("/api/v1/records/user:missing")
        assert res_missing.status_code == 404
        assert res_missing.json()["detail"] == "Key 'user:missing' not found"
        print("  GET lookups and 404 missing status handled.")

        print("\n[TEST 3] Testing DELETE /records/{key} tombstoning...")
        res_del = client.delete("/api/v1/records/user:alpha")
        assert res_del.status_code == 200
        assert res_del.json() == {"status": "deleted", "key": "user:alpha"}

        res_check = client.get("/api/v1/records/user:alpha")
        assert res_check.status_code == 404
        print("  DELETE route verified; key now returns 404.")

        print("\n[TEST 4] Testing POST /engine/flush and POST /engine/compact...")
        for i in range(20):
            client.put(f"/api/v1/records/batch:{i:03d}", json={"value": f"item_{i}"})

        res_flush1 = client.post("/api/v1/engine/flush")
        assert res_flush1.status_code == 200
        assert "Flushed to" in res_flush1.json()["message"]

        for i in range(20, 40):
            client.put(f"/api/v1/records/batch:{i:03d}", json={"value": f"item_{i}"})
        res_flush2 = client.post("/api/v1/engine/flush")
        assert res_flush2.status_code == 200

        res_metrics = client.get("/api/v1/engine/metrics")
        assert res_metrics.status_code == 200
        assert res_metrics.json()["sstable_count"] == 2
        print(f"  Pre-compaction metrics confirmed: {res_metrics.json()['sstable_count']} SSTables.")

        res_compact = client.post("/api/v1/engine/compact")
        assert res_compact.status_code == 200

        res_post_metrics = client.get("/api/v1/engine/metrics")
        assert res_post_metrics.json()["sstable_count"] == 1
        print("  Compaction endpoint reduced SSTables to 1.")

        res_verify_data = client.get("/api/v1/records/batch:015")
        assert res_verify_data.status_code == 200
        assert res_verify_data.json()["value"] == "item_15"
        print("  Data integrity preserved across compaction.")

    cleanup()
    print("-" * 65)
    print(" [STATUS] ALL MILESTONE 6 ASSERTIONS PASSED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    run_milestone6_tests()