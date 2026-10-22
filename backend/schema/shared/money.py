from decimal import Decimal
from typing import Annotated

from pydantic import Field

# All dispatcher-entered bids must represent an exact positive cent amount.
# The same Decimal is evaluated, persisted, and serialized to the board.
PositiveBid = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2, allow_inf_nan=False)]
