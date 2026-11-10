.PHONY: run scan docker up down clean

run:            ## запустить локально на :8000
	python3 app.py --port 8000

scan:           ## один скан без сервера → data/deals.csv
	python3 scripts/snapshot.py

docker:         ## собрать образ
	docker build -t dota-gem-monitor:local .

up:             ## поднять через compose (порт 8000, история в ./data)
	docker compose up -d --build

down:
	docker compose down

clean:
	rm -rf __pycache__ data monitor.db
