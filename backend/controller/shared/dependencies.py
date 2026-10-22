from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy import Engine as EngineType
from sqlalchemy.orm import Session

from service.shared.database import get_engine, get_session

Database = Annotated[Session, Depends(get_session)]
# WebSocket handlers use the engine instead of a session: a dependency that
# yields a session is only released when the socket closes, which would hold a
# pooled connection for the whole life of the connection.
Engine = Annotated[EngineType, Depends(get_engine)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
CleanupCount = Annotated[int, Query(ge=1, le=10000, description='Maximum number of oldest eligible history records to clear.')]
