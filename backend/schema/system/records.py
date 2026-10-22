"""Read-only views of the tables the dispatcher owns."""
from pydantic import BaseModel


class RecordTableSummary(BaseModel):
    table: str
    label: str
    description: str
    group: str
    row_count: int


class RecordPage(BaseModel):
    table: str
    label: str
    description: str
    columns: list[str]
    rows: list[list[str | None]]
    total: int
    limit: int
    offset: int
