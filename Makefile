.PHONY: up down logs test lint build

up:
	docker compose up --build -d --wait

down:
	docker compose down

logs:
	docker compose logs -f backend frontend model

test:
	cd backend && uv run --locked python -m unittest discover -s tests
	cd model && uv run --locked python -m unittest discover -s tests

lint:
	cd frontend && npm run lint && npx tsc -b

build:
	docker compose build
