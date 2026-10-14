import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status
from engine.lsm_tree import LSMTree
from api.schemas import (
    PutRecordRequest,
    RecordResponse,
    OperationStatusResponse,
    MessageResponse,
    EngineMetricsResponse,
)

DATA_DIRECTORY = os.getenv("STRATAKV_DATA_DIR", "stratakv_store")
engine: LSMTree | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    engine = LSMTree(data_dir=DATA_DIRECTORY)
    yield
    if engine:
        engine.close()

app = FastAPI(
    title="StrataKV Storage Engine",
    version="1.0.0",
    lifespan=lifespan,
)

def get_engine() -> LSMTree:
    if engine is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage engine is uninitialized",
        )
    return engine

@app.put(
    "/api/v1/records/{key}",
    response_model=OperationStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def put_record(key: str, payload: PutRecordRequest):
    storage = get_engine()
    storage.put(key, payload.value)
    return OperationStatusResponse(status="success", key=key)

@app.get(
    "/api/v1/records/{key}",
    response_model=RecordResponse,
    status_code=status.HTTP_200_OK,
)
async def get_record(key: str):
    storage = get_engine()
    value = storage.get(key)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Key '{key}' not found",
        )
    return RecordResponse(key=key, value=value)

@app.delete(
    "/api/v1/records/{key}",
    response_model=OperationStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_record(key: str):
    storage = get_engine()
    storage.delete(key)
    return OperationStatusResponse(status="deleted", key=key)

@app.post(
    "/api/v1/engine/flush",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def trigger_flush():
    storage = get_engine()
    flushed_table = storage.flush()
    if flushed_table:
        return MessageResponse(message=f"Flushed to {flushed_table}")
    return MessageResponse(message="MemTable empty; nothing to flush")

@app.post(
    "/api/v1/engine/compact",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
)
async def trigger_compaction():
    storage = get_engine()
    storage.compact()
    return MessageResponse(message="Compaction completed successfully")

@app.get(
    "/api/v1/engine/metrics",
    response_model=EngineMetricsResponse,
    status_code=status.HTTP_200_OK,
)
async def get_metrics():
    storage = get_engine()
    return EngineMetricsResponse(
        memtable_size_bytes=storage.memtable.size_bytes,
        memtable_entry_count=storage.memtable.entry_count,
        sstable_count=len(storage.sstables),
        active_sstables=[os.path.basename(s.base_path) for s in storage.sstables],
    )