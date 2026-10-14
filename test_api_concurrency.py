import os
import shutil
import asyncio
import httpx
import api.main as app_module

CONCURRENCY_TEST_DIR = "test_api_concurrency_store"

def cleanup():
    if os.path.exists(CONCURRENCY_TEST_DIR):
        shutil.rmtree(CONCURRENCY_TEST_DIR, ignore_errors=True)

async def run_concurrent_requests():
    cleanup()
    print("-" * 65)
    print(" STRATAKV API CONCURRENCY & ASYNC OFF-LOADING VERIFICATION ")
    print("-" * 65)

    app_module.DATA_DIRECTORY = CONCURRENCY_TEST_DIR

    async with app_module.lifespan(app_module.app):
        transport = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            print("[TEST 1] Firing 100 concurrent asynchronous PUT requests...")
            
            async def put_item(i: int):
                return await client.put(
                    f"/api/v1/records/concurrent:{i:04d}",
                    json={"value": f"payload_{i}_{'v'*100}"}
                )

            put_tasks = [put_item(i) for i in range(100)]
            put_responses = await asyncio.gather(*put_tasks)
            for r in put_responses:
                assert r.status_code == 200, f"Concurrent PUT failed with status {r.status_code}"
            print("  100 concurrent writes completed with HTTP 200.")

            print("\n[TEST 2] Firing 100 concurrent GET lookups interleaved with metrics calls...")
            
            async def get_item(i: int):
                return await client.get(f"/api/v1/records/concurrent:{i:04d}")

            async def get_metrics():
                return await client.get("/api/v1/engine/metrics")

            mixed_tasks = [get_item(i) for i in range(100)] + [get_metrics() for _ in range(10)]
            mixed_responses = await asyncio.gather(*mixed_tasks)

            for r in mixed_responses:
                assert r.status_code == 200, f"Mixed concurrent query failed: {r.status_code}"
            print("  110 interleaved concurrent reads and metric queries verified.")

            print("\n[TEST 3] Testing concurrent flush and continuous read workload...")
            flush_task = client.post("/api/v1/engine/flush")
            read_tasks = [get_item(i) for i in range(50)]
            
            results = await asyncio.gather(flush_task, *read_tasks)
            assert results[0].status_code == 200
            for r in results[1:]:
                assert r.status_code == 200
            print("  Concurrent flush did not deadlock or interrupt active queries.")

    cleanup()
    print("-" * 65)
    print(" [STATUS] API ASYNC CONCURRENCY VERIFIED SUCCESSFULLY")
    print("-" * 65)

if __name__ == "__main__":
    asyncio.run(run_concurrent_requests())