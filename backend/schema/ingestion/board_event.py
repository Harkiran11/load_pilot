from pydantic import Field

from schema.ingestion.board_api import BoardClock, BoardOrder, BoardPayload


class BoardEvent(BoardPayload):
    event: str = Field(min_length=1)
    order: BoardOrder | None = None
    orders: list[BoardOrder] | None = None
    clock: BoardClock | None = None
    stats: dict[str, int] | None = None
