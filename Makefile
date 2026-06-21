.PHONY: help up down restart logs build generate-data seed-data test test-local shell-db

help:
	@echo "Available commands:"
	@echo "  up            - Start all containers in the background"
	@echo "  down          - Stop and remove all containers"
	@echo "  restart       - Restart all containers"
	@echo "  logs          - Tail container logs"
	@echo "  build         - Rebuild the application containers"
	@echo "  generate-data - Generate the 100k+ query CSV dataset locally"
	@echo "  seed-data     - Populate the PostgreSQL database with the generated dataset"
	@echo "  test          - Run the backend test suite inside Docker"
	@echo "  shell-db      - Enter the PostgreSQL CLI in the database container"

up:
	docker-compose up -d

down:
	docker-compose down -v

restart:
	docker-compose restart

logs:
	docker-compose logs -f

build:
	docker-compose build

generate-data:
	python3 backend/scripts/generate_dataset.py

seed-data:
	docker-compose exec web python scripts/ingest_data.py

test:
	docker-compose exec web pytest -v

shell-db:
	docker-compose exec db psql -U typeahead_user -d typeahead_db
