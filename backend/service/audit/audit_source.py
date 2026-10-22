"""One explicit capture source flag for both server and offline entry points."""
import os

from dotenv import dotenv_values

from service.shared.database import PROJECT_ROOT


def synthetic_source():
    config = dotenv_values(PROJECT_ROOT / '.env')
    value = os.environ.get('LIVE_AUDIT_SYNTHETIC', config.get('LIVE_AUDIT_SYNTHETIC', 'false'))
    if not isinstance(value, str) or value.strip().lower() not in ('true', 'false'):
        raise ValueError('LIVE_AUDIT_SYNTHETIC must be true or false; capture source cannot be inferred')
    return value.strip().lower() == 'true'
