from schema.system.health_schema import HealthResponse


def get_health() -> HealthResponse:
    return HealthResponse(status='ok')
