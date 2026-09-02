from sentinel.api.routers import alerts, evidence, mapview, search, traffic, violations

ROUTERS = [
    mapview.router,
    search.router,
    alerts.router,
    violations.router,
    traffic.router,
    evidence.router,
]
