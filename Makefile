.PHONY: up down test test-unit test-integration logs build migrate shell

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

migrate:
	docker compose run --rm api sh -c "cd /app/api && python -m alembic upgrade head"

test-unit:
	pytest tests/unit/ -v -m unit

test-integration:
	pytest tests/integration/ -v -m integration

test:
	make test-unit && make test-integration

test-docker:
	docker compose run --rm api pytest /app/tests/ -v

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker

shell:
	docker compose exec api bash
