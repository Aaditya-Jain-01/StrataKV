from pydantic import BaseModel, Field

class PutRecordRequest(BaseModel):
    value: str = Field(..., min_length=1)

class RecordResponse(BaseModel):
    key: str
    value: str

class OperationStatusResponse(BaseModel):
    status: str
    key: str

class MessageResponse(BaseModel):
    message: str

class EngineMetricsResponse(BaseModel):
    memtable_size_bytes: int
    memtable_entry_count: int
    sstable_count: int
    active_sstables: list[str]