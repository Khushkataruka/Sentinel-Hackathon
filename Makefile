# Local development uses uv; the stack itself runs in compose.
UV      ?= uv
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help install db-up db-down migrate seed test lint \
        build up down logs ps registry ingest pipelines correlation api \
        sync survey preflight clean

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# -- local ------------------------------------------------------------------
install:  ## Sync the workspace into .venv
	$(UV) sync --all-extras

test:  ## Run the test suite (DB-backed tests skip with no database)
	$(UV) run pytest -q

lint:  ## ruff
	$(UV) run ruff check .

# -- database ---------------------------------------------------------------
db-up:  ## Start postgres (postgis + pgvector) only
	$(COMPOSE) up -d postgres

db-down:  ## Stop postgres
	$(COMPOSE) stop postgres

migrate:  ## Apply migrations
	$(COMPOSE) run --rm migrate python scripts/migrate.py

seed:  ## Apply migrations and development seed data
	$(COMPOSE) run --rm migrate python scripts/migrate.py --seed

# -- stack ------------------------------------------------------------------
build:  ## Build the images (first run exports YOLOv8n, and is slow)
	$(COMPOSE) build

up:  ## Bring up the whole stack; frontend on :8080
	$(COMPOSE) up -d

down:  ## Stop everything
	$(COMPOSE) down

ps:  ## Service status
	$(COMPOSE) ps

logs:  ## Tail logs (S=service to narrow)
	$(COMPOSE) logs -f $(S)

registry ingest pipelines correlation api:  ## Run one service in the foreground
	$(COMPOSE) up $@

# -- onboarding the grid ----------------------------------------------------
sync:  ## Upsert cameras from the sandbox catalogue (needs URL + cookie)
	$(COMPOSE) run --rm registry \
	  sentinel-registry sync-sentinel --department-id $(or $(DEPT),1)

preflight:  ## Measure one camera: real fps, PTS behaviour, loop period. C=id
	$(COMPOSE) run --rm ingest \
	  sentinel-ingest preflight $(C) --seconds $(or $(SECONDS),180) --json

survey:  ## Record provisional profiles so the pipelines are not all skipped
	$(COMPOSE) run --rm registry python scripts/survey.py $(ARGS)

clean:  ## Remove containers and volumes. Destroys the database.
	$(COMPOSE) down -v
