from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from controller.system.health_controller import router as health_router
from controller.orders.order_controller import router as order_router
from controller.audit.activity_controller import router as activity_router
from controller.orders.bid_controller import router as bid_router
from controller.fleet.fleet_controller import legacy_router as legacy_fleet_router
from controller.fleet.fleet_controller import router as fleet_router
from controller.orders.load_controller import router as load_router
from controller.analytics.overview_controller import router as overview_router
from controller.pricing.price_analysis_controller import router as price_analysis_router
from controller.autopilot.autopilot_controller import router as autopilot_router
from controller.analytics.analytics_controller import router as analytics_router
from controller.audit.live_audit_controller import router as live_audit_router
from controller.audit.monitoring_controller import router as monitoring_router
from controller.allocation.allocation_controller import router as allocation_router
from controller.orders.shipment_controller import router as shipment_router
from controller.system.system_controller import router as system_router
from controller.terminal.terminal_controller import router as terminal_router
from service.shared.errors import ProductError
from startup import lifespan

app = FastAPI(title='LoadPilot API', version='0.1.0', lifespan=lifespan)
app.include_router(health_router)
app.include_router(order_router)
app.include_router(fleet_router)
app.include_router(legacy_fleet_router)
app.include_router(terminal_router)
app.include_router(load_router)
app.include_router(bid_router)
app.include_router(shipment_router)
app.include_router(activity_router)
app.include_router(overview_router)
app.include_router(system_router)
app.include_router(price_analysis_router)
app.include_router(autopilot_router)
app.include_router(analytics_router)
app.include_router(live_audit_router)
app.include_router(monitoring_router)
app.include_router(allocation_router)


@app.exception_handler(ProductError)
async def product_error(request: Request, exc: ProductError):
    content = {'detail': exc.detail}
    if exc.evaluation is not None:
        content['evaluation'] = exc.evaluation
    if exc.feedback is not None:
        content['feedback'] = exc.feedback
    return JSONResponse(status_code=exc.status_code, content=content)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('main:app', host='127.0.0.1', port=8000, reload=True)
