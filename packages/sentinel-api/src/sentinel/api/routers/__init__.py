from sentinel.api.routers import (
    alerts,
    annotated,
    evidence,
    live_sightings,
    mapview,
    search,
    traffic,
    violations,
)

ROUTERS = [
    annotated.router,
    mapview.router,
    search.router,
    alerts.router,
    violations.router,
    traffic.router,
    evidence.router,
    live_sightings.router,
]
