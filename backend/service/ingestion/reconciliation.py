import asyncio
from decimal import Decimal
from urllib.parse import quote

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from schema.ingestion.board_api import BoardOrder
from schema.orders.shipment import Shipment
from schema.dispatch.dispatch_action import DispatchAction


def known_order_ids(engine: Engine) -> set[str]:
    with Session(engine) as session:
        return set(session.scalars(select(Shipment.external_id).where(Shipment.board_retired_at.is_(None))))


async def reconcile_known_orders(
    client: httpx.AsyncClient, engine: Engine, snapshot: list[BoardOrder],
    tracked_order_ids: set[str] | None = None,
) -> tuple[list[BoardOrder], set[str]]:
    """Recover live orders without re-fetching the entire closed archive per arrival."""
    known_ids = await asyncio.to_thread(known_order_ids, engine)
    snapshot_ids = {order.external_id for order in snapshot}
    absent_ids = known_ids - snapshot_ids
    def tracked_ids():
        with Session(engine) as session:
            active = list(session.execute(select(Shipment.external_id, Shipment.status).where(
                Shipment.board_retired_at.is_(None),
                Shipment.status.in_(('open', 'bid_submitted', 'accepted', 'in_transit')),
            )))
            uncertain = set(session.scalars(select(Shipment.external_id).join(DispatchAction).where(
                Shipment.board_retired_at.is_(None),
                DispatchAction.request_status.in_(('submitting', 'unknown')),
            )))
            live_ids = {external_id for external_id, _ in active} | uncertain
            tracked = {external_id for external_id, status in active if status != 'open'} | uncertain
            return live_ids, tracked

    live_ids, tracked = await asyncio.to_thread(tracked_ids)
    if known_ids & snapshot_ids:
        # Closed history is retained locally, but cannot change fleet capacity.
        # Keep ordinary passes bounded by current offers/bookings and uncertain
        # actions instead of thousands of completed/expired detail requests.
        absent_ids &= live_ids
    # If no known identity remains in the snapshot, retain the exhaustive 404
    # check. Reset retirement still requires proof that ALL old orders vanished;
    # a clock rewind or a capped list alone must never release locks.
    tracked.update(tracked_order_ids or ())
    tracked.update(order.external_id for order in snapshot if order.status in ('bid_submitted', 'accepted', 'in_transit'))
    semaphore = asyncio.Semaphore(8)

    async def fetch_order(external_id: str) -> tuple[str, BoardOrder | None]:
        async with semaphore:
            response = await client.get('orders/' + quote(external_id, safe=''))
            if response.status_code == 404:
                return external_id, None
            response.raise_for_status()
            order = BoardOrder.model_validate(response.json(parse_float=Decimal))
            if order.external_id != external_id:
                raise ValueError('Board returned a different external_id during reconciliation')
            return external_id, order

    results = await asyncio.gather(*(fetch_order(external_id) for external_id in sorted(absent_ids | tracked)))
    recovered = [order for _, order in results if order is not None]
    missing_ids = {external_id for external_id, order in results if order is None}
    merged = {order.external_id: order for order in snapshot if order.external_id not in missing_ids}
    merged.update({order.external_id: order for order in recovered})
    return list(merged.values()), missing_ids
