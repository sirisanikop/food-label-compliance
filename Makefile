.PHONY: up down scale3 logs test contracts ingest eval load clean

up:
	docker compose up --build -d

down:
	docker compose down

scale3:
	docker compose up --build -d --scale worker=3

logs:
	docker compose logs -f --tail=100

test:
	python -m pytest

contracts:
	python scripts/export_contracts.py

ingest:
	docker compose run --rm indexer

eval:
	docker compose run --rm worker python -m foodlabel.evaluation.run_eval --set eval/eval_set.jsonl

load:
	docker compose --profile loadtest run --rm locust

clean:
	docker compose down -v
