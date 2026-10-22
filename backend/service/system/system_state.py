from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ConnectionState:
    status: str = 'disconnected'
    changed_at: datetime | None = None
    last_error: str | None = None

    def set(self, status: str, error: str | None = None) -> None:
        self.status = status
        self.changed_at = datetime.now(timezone.utc)
        self.last_error = error


connection_state = ConnectionState()
