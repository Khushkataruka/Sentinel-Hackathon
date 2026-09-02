from sentinel.registry.routers import (
    access,
    adapters,
    cameras,
    departments,
    health,
    imports,
    profiles,
    queues,
)

ROUTERS = [
    departments.router,
    cameras.router,
    profiles.router,
    adapters.router,
    health.router,
    access.router,
    imports.router,
    queues.router,
]
