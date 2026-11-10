# Dota 2 · Монитор «аркана + самоцвет»

[![CI](https://github.com/USER/dota-gem-monitor/actions/workflows/ci.yml/badge.svg)](../../actions/workflows/ci.yml)
[![Docker](https://github.com/USER/dota-gem-monitor/actions/workflows/docker-publish.yml/badge.svg)](../../actions/workflows/docker-publish.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

Мониторит Steam Community Market в реальном времени: обходит **все** лоты аркан с прайзматическим
сокетом, вытаскивает из карточки каждого лота **состав сокетов** (какой гем уже вставлен), забирает цены
всех прайзматических гемов и считает выгоду:

```
выгода = (самый дешёвый лот арканы + самый дешёвый лот этого гема) − цена готового лота
```

Ищет ровно тот случай, когда «аркана за 3к + гем за 6к» продаётся готовым лотом за 5к.
Работает на двух арканах — `Fractal Horns of Inner Abysm` (Terrorblade) и `Swine of the Sunken Galley`
(Techies): только у них в игре есть прайзматический сокет.

## Что умеет

- **Живые данные** — скан раз в 15 минут, страница обновляется сама каждые 8 секунд.
- **3 карточки-лидера** — максимум выгоды в ₽, лучший процент, самый дешёвый вход с выгодой ≥15%.
- **Фильтры** — аркана, поиск по гему, цена до, выгода от %, «скрыть дефолтные гемы», «только новые лоты».
- **История в SQLite** — колонка `Δ` с изменением цены гема с прошлого скана и бейдж **NEW** у лотов,
  которых раньше не было.
- **CSV-экспорт** со ссылками на аркану и на гем (`/api/export.csv`).
- **Ноль зависимостей** — только стандартная библиотека Python 3.9+ и `curl`.
- **Фронтенд самодостаточный** — ни одного внешнего CDN/шрифта, открывается в закрытых сетях.

## Быстрый старт

```bash
git clone https://github.com/USER/dota-gem-monitor.git
cd dota-gem-monitor

# вариант 1: локально
python3 app.py --port 8000          # → http://localhost:8000

# вариант 2: docker
docker build -t dota-gem-monitor .
docker run --rm -p 8000:8000 -v "$PWD/data:/data" dota-gem-monitor

# вариант 3: compose (история в ./data, рестарт при падении)
docker compose up -d --build

# один скан без сервера → data/deals.csv
python3 scripts/snapshot.py
```

Первый скан занимает ~70 секунд (Steam, 25 запросов с троттлингом) — таблица заполнится сама.

## Переменные окружения

| Переменная | По умолчанию | Что делает |
|---|---|---|
| `PORT` | `8000` | порт HTTP-сервера |
| `SCAN_INTERVAL` | `900` | интервал автоскана, сек. Меньше 300 — риск получить 429 от Steam |
| `DB_PATH` | `./monitor.db` | путь к SQLite с историей (в Docker — `/data/monitor.db`) |
| `NO_SCAN` | `0` | `1` — поднять только UI на уже собранной истории |

Флаги CLI дублируют env: `--port`, `--interval`, `--no-scan`.

## API

| Эндпоинт | Ответ |
|---|---|
| `GET /api/state` | JSON: лоты с выгодой, цены гемов, `Δ`, база по арканам, статус скана |
| `GET /api/scan` | запустить скан в фоне (если не идёт и прошло 45 с) |
| `GET /api/export.csv` | CSV всех лотов со ссылками (`;`, UTF-8 BOM — Excel открывает сразу) |
| `GET /api/health` | `{"ok":true,...}` — для healthcheck Docker/Render |

Пример:

```bash
curl -s localhost:8000/api/state | jq '.deals[:3] | .[] | {hero, gem, price_usd, save_pct}'
```

## Деплой

**Docker / GHCR.** Workflow `docker-publish.yml` собирает образ на каждый пуш в основную ветку
и на теги `v*`, публикует в `ghcr.io/USER/dota-gem-monitor`:

```bash
docker run -d --name monitor -p 8000:8000 \
  -e SCAN_INTERVAL=900 -v "$PWD/data:/data" \
  ghcr.io/USER/dota-gem-monitor:latest
```

**Render** — в репозитории лежит `render.yaml` (Blueprint): *New → Blueprint → выбрать репо*.
Нужен план не ниже `starter` и диск на 1 ГБ (на free-плане сервис засыпает и мониторинг встаёт).

**VPS** — `docker compose up -d`, дальше nginx/Caddy как reverse proxy на `127.0.0.1:8000`.

**Fly.io / Railway** — Dockerfile стандартный, достаточно указать порт из `PORT` и постоянный volume на `/data`.

> **GitHub Pages не подойдёт:** это Python-сервер с фоновым сканом, а не статика.

## Workflows

| Файл | Что делает |
|---|---|
| `ci.yml` | `ruff` + `py_compile`, проверка что в `index.html` нет внешних CDN, сборка образа и smoke-test `/api/health` |
| `docker-publish.yml` | сборка и публикация образа в GHCR (ветка по умолчанию → `latest`, теги → версии) |
| `daily-snapshot.yml` | по расписанию (06:00 UTC) делает скан без сервера, кладёт топ-10 в summary запуска и CSV в артефакты |

Ежедневный срез можно запустить руками: *Actions → Ежедневный срез цен → Run workflow*.

## Как считается выгода (и где легко обмануться)

- **База** — самый дешёвый лот арканы прямо сейчас; **гем** — самый дешёвый лот этого гема на Steam.
  `%` считается от суммы «аркана + гем», то есть это скидка к сборке того же вида.
- **Дефолтные гемы не считаются апгрейдом.** У Terrorblade штатный гем `Prismatic: Reflection's Shade`
  (~$6), у Techies — `Prismatic: Explosive Burst` (~$31). Лоты с ними помечаются `дефолт` и по умолчанию
  скрыты, иначе монитор показывал бы фальшивую «выгоду» на каждом втором лоте Techies.
- **«Выгода» ≠ прибыль.** При замене гема старый уничтожается, вынуть его обратно можно только Мастерским
  молотом (он уничтожает аркану), а Steam берёт 15% при продаже. Это экономия на сборке, а не арбитраж.
- **Лоты уникальны** — конкретный лот с нужным гемом уйдёт; монитор показывает срез, а не резерв.

## Технические заметки

- Steam отдаёт **429 на python-urllib** (режет TLS-отпечаток), `curl` проходит — поэтому весь HTTP идёт
  через `curl` с троттлингом 2,6 с между запросами и паузой 20 с при 429.
- Параллельные запросы ловят бан быстрее последовательных, поэтому скан однопоточный.
- Сокеты приходят в HTML-странице лота сильно экранированным JSON — парсер чистит экранирование
  и вытаскивает `имя гема + Prismatic Gem` из блока сокета.
- Частичный сбой страницы не роняет скан: пропуск логируется, остальные лоты остаются. Полный сбой —
  интерфейс показывает ошибку и данные прошлого успешного скана.

## Структура

```
.
├── app.py                      # бэкенд: парсер Steam + расчёт + HTTP API + фоновый скан
├── index.html                  # весь фронтенд (инлайн CSS/JS, без внешних зависимостей)
├── scripts/snapshot.py         # один скан без сервера (для Actions / cron)
├── Dockerfile · docker-compose.yml · render.yaml
├── Makefile                    # make run / scan / docker / up / down
├── .github/workflows/          # ci · docker-publish · daily-snapshot
└── monitor.db                  # создаётся на лету, в git не попадает
```

## Дисклеймер

Неофициальный проект, не связан с Valve. Данные — с публичных страниц Steam Community Market,
без авторизации и без доступа к чужим аккаунтам. Держите разумный интервал скана (≥5 минут),
чтобы не создавать нагрузку и не попасть под rate-limit. Покупая предметы, вы соглашаетесь
с [Steam Subscriber Agreement](https://store.steampowered.com/subscriber_agreement/).

Лицензия — [MIT](LICENSE).
