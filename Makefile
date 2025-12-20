DEV_BACKEND_IMAGE=clinnexus-backend
DEV_FRONTEND_IMAGE=clinnexus-frontend

# Development команды
.PHONY: dev
dev:
	docker-compose up --build

.PHONY: migrate
migrate:
	docker-compose run --rm backend alembic -c /app/alembic.ini upgrade head

.PHONY: seed
seed:
	docker-compose run --rm backend python -m app.scripts.seed

.PHONY: seed-taxonomy
seed-taxonomy:
	docker-compose run --rm backend python -m app.scripts.seed_taxonomy

# Production команды
.PHONY: prod-build
prod-build:
	docker compose -f docker-compose.prod.yml build

.PHONY: prod-up
prod-up:
	docker compose -f docker-compose.prod.yml up -d

.PHONY: prod-down
prod-down:
	docker compose -f docker-compose.prod.yml down

.PHONY: prod-logs
prod-logs:
	docker compose -f docker-compose.prod.yml logs -f

.PHONY: prod-ps
prod-ps:
	docker compose -f docker-compose.prod.yml ps

.PHONY: prod-restart
prod-restart:
	docker compose -f docker-compose.prod.yml restart

.PHONY: prod-migrate
prod-migrate:
	docker compose -f docker-compose.prod.yml run --rm backend alembic -c /app/alembic.ini upgrade head

.PHONY: prod-seed
prod-seed:
	docker compose -f docker-compose.prod.yml run --rm backend python -m app.scripts.seed

.PHONY: prod-seed-taxonomy
prod-seed-taxonomy:
	docker compose -f docker-compose.prod.yml run --rm backend python -m app.scripts.seed_taxonomy

.PHONY: prod-seed-taxonomy-clear
prod-seed-taxonomy-clear:
	docker compose -f docker-compose.prod.yml run --rm backend python -m app.scripts.seed_taxonomy --clear

.PHONY: prod-clean
prod-clean:
	docker compose -f docker-compose.prod.yml down -v
	docker system prune -f


