"""Versioned model-service contracts; the installed local package owns their format."""
from loadpilot_model.api.autopilot import AutopilotRequest as PairRequest
from loadpilot_model.api.live import LiveBatchRequest as ModelBatchRequest
from loadpilot_model.api.live import LiveBatchResponse as ModelBatchResponse

__all__ = ['PairRequest', 'ModelBatchRequest', 'ModelBatchResponse']
