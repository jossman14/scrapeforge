.PHONY: up down test logs build

up:
	docker compose up -d

down:
	docker compose down

test:
	docker compose run --rm api pytest tests/ -v

logs:
	docker compose logs -f

build:
	docker compose build --no-cache
