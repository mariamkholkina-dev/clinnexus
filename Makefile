DEV_BACKEND_IMAGE=clinnexus-backend
DEV_FRONTEND_IMAGE=clinnexus-frontend

.PHONY: dev
dev:
	docker-compose up --build

.PHONY: migrate
migrate:
	docker-compose run --rm backend alembic -c /app/db/alembic.ini upgrade head

.PHONY: seed
seed:
	docker-compose run --rm backend python -m app.scripts.seed


