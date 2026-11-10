#!/usr/bin/env python3
"""
Dota 2 Arcana Gem Monitor
=========================
Мониторит Steam Community Market в реальном времени:
  * все лоты аркан с прайзматическим сокетом (Terrorblade / Techies)
  * цены всех прайзматических гемов
  * считает выгоду: (дешёвая аркана + гем отдельно) - цена готового лота

Только stdlib. Запуск:  python3 app.py --port 8000
"""
import argparse
import csv
import io
import json
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(ROOT, "monitor.db"))

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

ARCANAS = [
    {"key": "tb", "hero": "Terrorblade", "name": "Fractal Horns of Inner Abysm",
     "default_gem": "Reflection's Shade", "accent": "#c2554d"},
    {"key": "te", "hero": "Techies", "name": "Swine of the Sunken Galley",
     "default_gem": "Explosive Burst", "accent": "#d8a13a"},
]

RATE_FALLBACK = 82.57          # ₽ за $1 по внутреннему курсу Steam
RATE = RATE_FALLBACK
SCAN_INTERVAL = int(os.environ.get("SCAN_INTERVAL", "900"))   # сек между автосканами
MIN_MANUAL_GAP = 45            # сек, чтобы не спамить Steam кнопкой
REQ_DELAY = 2.6                # сек между запросами к Steam (иначе 429)

_print_lock = threading.Lock()


def log(*a):
    with _print_lock:
        print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# --------------------------------------------------------------------------- net
_throttle_lock = threading.Lock()
_last_req = [0.0]


def http_get(url, tries=3):
    """GET через curl: Steam режет python-urllib по TLS-отпечатку (429)."""
    import subprocess
    last = None
    for attempt in range(tries):
        with _throttle_lock:
            wait = REQ_DELAY - (time.time() - _last_req[0])
            if wait > 0:
                time.sleep(wait)
            _last_req[0] = time.time()
        cmd = ["curl", "-sS", "-m", "45",
               "-H", f"User-Agent: {UA}",
               "-H", "Accept: */*",
               "-w", "\n%{http_code}", url]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=60)
            out = p.stdout.decode("utf-8", "replace")
            body, _, code = out.rpartition("\n")
            if code.strip() == "200":
                return body
            if code.strip() == "429":
                log("429, пауза 20с")
                time.sleep(20)
            last = RuntimeError(f"HTTP {code.strip()} {url[:80]}")
        except Exception as e:  # noqa: BLE001
            last = e
        if attempt < tries - 1:
            log("retry", attempt + 1, type(last).__name__, str(last)[:90])
            time.sleep(2.0 * (attempt + 1))
    raise last if last else RuntimeError("fetch failed")


def unesc(s):
    """Страница Steam отдаёт JSON дважды/трижды экранированным."""
    s = s.replace('\\\\\\"', '\x01').replace('\\\\"', '"').replace('\\"', '"')
    s = s.replace('\x01', '"')
    return (s.replace('\\u003c', '<').replace('\\u003e', '>')
             .replace('\\u0026', '&').replace('\\/', '/'))


# ------------------------------------------------------------------------ парсер
SOCKET_RE = re.compile(r'>([^<]{2,48})</span><br><span style="font-size: 12px">([^<]+)</span>')


def parse_listings(html, hero_key):
    """-> (total_count|None, [ {id, price, sockets, gem} ])"""
    total = None
    m = re.search(r'total_count[^0-9]{1,8}(\d{1,7})', html)
    if m:
        total = int(m.group(1))

    rows = []
    for chunk in html.split('listingid')[1:]:
        sub = unesc(chunk[:20000])
        mid = re.search(r'(\d{10,20})', chunk[:80])
        mp = re.search(r'strSubtotal":"(\$[\d,\.]+)"', sub)
        if not mp:
            continue
        price = float(mp.group(1).replace('$', '').replace(',', ''))
        sockets = SOCKET_RE.findall(sub)
        gem = None
        for gname, gtype in sockets:
            if gtype.strip().lower() == 'prismatic gem':
                gem = gname.strip()
                break
        rows.append({
            "id": mid.group(1) if mid else None,
            "price": price,
            "sockets": [{"name": a, "type": b} for a, b in sockets],
            "gem": gem,
            "hero": hero_key,
        })
    # дедуп по listing id (страницы иногда пересекаются)
    seen, uniq = set(), []
    for r in rows:
        k = r["id"] or (r["price"], r["gem"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return total, uniq


def listing_url(name):
    return "https://steamcommunity.com/market/listings/570/" + urllib.parse.quote(name)


def gem_url(gem):
    return listing_url("Prismatic: " + gem)


def scrape_arcana(arc, progress):
    name = arc["name"]
    base = ("https://steamcommunity.com/market/listings/570/" +
            urllib.parse.quote(name) + "/render/")
    url0 = base + "?query=&start=0&count=20&currency=1&language=english&format=json"
    html = http_get(url0)
    total, first = parse_listings(html, arc["key"])
    rows = list(first)
    progress["pages"] += 1

    step = 20
    starts = list(range(step, total or 0, step))[:40]
    for start in starts:
        u = base + f"?query=&start={start}&count=20&currency=1&language=english&format=json"
        try:
            rows += parse_listings(http_get(u, tries=3), arc["key"])[1]
        except Exception as e:  # noqa: BLE001
            log(f"пропущена страница {start}: {type(e).__name__}")
        progress["pages"] += 1

    seen, uniq = set(), []
    for r in rows:
        k = r["id"] or (r["price"], r["gem"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    uniq.sort(key=lambda r: r["price"])
    log(f"{arc['hero']}: {len(uniq)} лотов (total={total})")
    return uniq


def scrape_gems(progress):
    """Все прайзматические гемы: имя -> (usd, кол-во лотов)."""
    out = {}
    start = 0
    page = 0
    while start < 200 and page < 12:
        url = (f"https://steamcommunity.com/market/search/render/?query=prismatic"
               f"&appid=570&norender=1&count=10&start={start}&currency=1")
        try:
            d = json.loads(http_get(url, tries=2))
        except Exception as e:  # noqa: BLE001
            log("gems page fail", start, type(e).__name__)
            break
        res = d.get("results") or []
        for r in res:
            n = r.get("hash_name", "")
            if n.startswith("Prismatic: "):
                out[n[11:]] = (r["sell_price"] / 100.0, r.get("sell_listings") or 0)
        total = d.get("searchdata", {}).get("total_count") or 0
        progress["pages"] += 1
        page += 1
        start += 10
        if start >= total:
            break
    log(f"гемов: {len(out)}")
    return out


def fetch_rate():
    """Внутренний курс Steam ₽/$: одна позиция в двух валютах."""
    try:
        a = json.loads(http_get("https://steamcommunity.com/market/priceoverview/"
                                "?appid=570&currency=1&market_hash_name=Prismatic%3A%20Orange", tries=1))
        b = json.loads(http_get("https://steamcommunity.com/market/priceoverview/"
                                "?appid=570&currency=5&market_hash_name=Prismatic%3A%20Orange", tries=1))
        usd = float(re.sub(r'[^\d.]', '', a["lowest_price"].replace(",", ".")))
        rub = float(re.sub(r'[^\d.,]', '', b["lowest_price"]).replace(",", ".").replace(" ", ""))
        if 30 < rub / usd < 400:
            return round(rub / usd, 2)
    except Exception:  # noqa: BLE001
        pass
    return None


# ------------------------------------------------------------------------- база
def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("""CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS gems(name TEXT, ts INTEGER, usd REAL, n INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lots(hero TEXT, lid TEXT, first_seen INTEGER,
                    last_seen INTEGER, price REAL, gem TEXT, PRIMARY KEY(hero, lid))""")
    conn.execute("CREATE INDEX IF NOT EXISTS i_gems ON gems(name, ts)")
    return conn


def store(ts, gems, lots_by_hero):
    conn = db()
    try:
        cur = conn.execute("INSERT INTO scans(ts) VALUES (?)", (ts,))
        scan_id = cur.lastrowid
        conn.executemany("INSERT INTO gems(name, ts, usd, n) VALUES (?,?,?,?)",
                         [(k, ts, v[0], v[1]) for k, v in gems.items()])
        new_ids = set()
        for hero, lots in lots_by_hero.items():
            for l in lots:
                if not l["id"]:
                    continue
                row = conn.execute("SELECT 1 FROM lots WHERE hero=? AND lid=?",
                                   (hero, l["id"])).fetchone()
                if row:
                    conn.execute("UPDATE lots SET last_seen=?, price=?, gem=? WHERE hero=? AND lid=?",
                                 (ts, l["price"], l["gem"], hero, l["id"]))
                else:
                    conn.execute("INSERT INTO lots(hero, lid, first_seen, last_seen, price, gem) "
                                 "VALUES (?,?,?,?,?,?)",
                                 (hero, l["id"], ts, ts, l["price"], l["gem"]))
                    new_ids.add((hero, l["id"]))
        conn.commit()
        return scan_id, new_ids
    finally:
        conn.close()


def gem_deltas():
    """Δ цены гема с прошлого скана."""
    conn = db()
    try:
        rows = conn.execute("""
            SELECT g.name, g.ts, g.usd FROM gems g
            JOIN (SELECT name, MAX(ts) m FROM gems GROUP BY name) m
              ON g.name = m.name AND g.ts = m.m ORDER BY g.name""").fetchall()
        prev = {}
        for name in {r[0] for r in rows}:
            two = conn.execute("SELECT ts, usd FROM gems WHERE name=? ORDER BY ts DESC LIMIT 2",
                               (name,)).fetchall()
            if len(two) == 2 and two[0][1] and two[1][1]:
                prev[name] = (two[0][1], two[0][1] - two[1][1])
        return prev
    finally:
        conn.close()


# ------------------------------------------------------------------------- скан
STATE = {
    "scanning": False,
    "progress": {"label": "ожидание", "pages": 0},
    "last_scan": None,
    "last_error": None,
    "deals": [],
    "gems": [],
    "lots": {},
    "base": {},
    "rate": RATE_FALLBACK,
    "scan_count": 0,
}
_state_lock = threading.Lock()


def run_scan(manual=False):
    if STATE["scanning"]:
        return False
    if manual and STATE["last_scan"] and time.time() - STATE["last_scan"] < MIN_MANUAL_GAP:
        return False
    STATE["scanning"] = True
    t0 = time.time()
    try:
        progress = STATE["progress"]
        progress.update({"label": "гемы", "pages": 0})
        rate = fetch_rate()
        gems = scrape_gems(progress)

        lots_by_hero, base = {}, {}
        for arc in ARCANAS:
            progress["label"] = f"лоты {arc['hero']}"
            lots = scrape_arcana(arc, progress)
            if not lots:
                raise RuntimeError(f"пустой ответ Steam по {arc['hero']}")
            lots_by_hero[arc["key"]] = lots
            base[arc["key"]] = min(l["price"] for l in lots)

        ts = int(time.time())
        _, new_ids = store(ts, gems, lots_by_hero)
        r = rate or RATE

        deals = []
        for arc in ARCANAS:
            hero = arc["key"]
            for l in lots_by_hero[hero]:
                gem = l["gem"]
                if not gem or gem not in gems:
                    continue
                gprice = gems[gem][0]
                setp = base[hero] + gprice
                save = setp - l["price"]
                deals.append({
                    "hero": arc["hero"],
                    "hero_key": hero,
                    "accent": arc["accent"],
                    "gem": gem,
                    "price_usd": round(l["price"], 2),
                    "gem_usd": round(gprice, 2),
                    "gem_n": gems[gem][1],
                    "base_usd": round(base[hero], 2),
                    "set_usd": round(setp, 2),
                    "save_usd": round(save, 2),
                    "save_pct": round(100 * save / setp, 1) if setp else 0,
                    "is_default": gem == arc["default_gem"],
                    "is_new": (hero, l["id"]) in new_ids,
                    "sockets": l["sockets"],
                    "gem_url": gem_url(gem),
                    "lot_url": listing_url(arc["name"]),
                })
        deals.sort(key=lambda d: -d["save_usd"])

        gems_out = [{
            "name": k, "usd": round(v[0], 2), "n": v[1],
            "url": gem_url(k),
        } for k, v in sorted(gems.items(), key=lambda kv: -kv[1][0])]

        with _state_lock:
            STATE.update({
                "deals": deals, "gems": gems_out, "lots": lots_by_hero,
                "base": {k: round(v, 2) for k, v in base.items()},
                "rate": r, "last_scan": ts, "last_error": None,
                "scan_count": STATE["scan_count"] + 1,
                "took": round(time.time() - t0, 1),
            })
        log(f"скан готов за {round(time.time()-t0,1)}с: {len(deals)} лотов с гемом")
    except Exception as e:  # noqa: BLE001
        log("ОШИБКА СКАНА:", type(e).__name__, e)
        with _state_lock:
            STATE["last_error"] = f"{type(e).__name__}: {e}"
    finally:
        STATE["scanning"] = False
    return True


def bg_loop(interval):
    # первый скан сразу
    run_scan()
    while True:
        time.sleep(10)
        if not STATE["scanning"] and STATE["last_scan"] and \
                time.time() - STATE["last_scan"] > interval:
            run_scan()


def deals_csv(rate, deals):
    """CSV со всеми лотами и ссылками. Используется API и scripts/snapshot.py."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Герой", "Самоцвет", "Цена лота, $", "Цена лота, ₽", "Гем отдельно, ₽",
                "Собрать самому, ₽", "Выгода, ₽", "Выгода, %", "Гемов на рынке",
                "Дефолтный гем", "Лоты арканы", "Гем"])
    for d in deals:
        w.writerow([d["hero"], d["gem"], d["price_usd"], round(d["price_usd"] * rate),
                    round(d["gem_usd"] * rate), round(d["set_usd"] * rate),
                    round(d["save_usd"] * rate), d["save_pct"], d["gem_n"],
                    "да" if d["is_default"] else "нет", d["lot_url"], d["gem_url"]])
    return buf.getvalue()


# ------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "DotaGemMonitor/1.0"

    def log_message(self, *a):  # тише в консоли
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            with open(ROOT + "/index.html", encoding="utf-8") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
            return
        if path == "/api/state":
            with _state_lock:
                payload = {
                    "ok": True,
                    "now": int(time.time()),
                    "scanning": STATE["scanning"],
                    "progress": STATE["progress"],
                    "last_scan": STATE["last_scan"],
                    "took": STATE.get("took"),
                    "error": STATE["last_error"],
                    "rate": STATE["rate"],
                    "base": STATE["base"],
                    "deals": STATE["deals"],
                    "gems": STATE["gems"],
                    "arcanas": [{"key": a["key"], "hero": a["hero"], "name": a["name"],
                                 "url": listing_url(a["name"]), "default_gem": a["default_gem"],
                                 "accent": a["accent"], "lots": len(STATE["lots"].get(a["key"], []))}
                                for a in ARCANAS],
                    "interval": SCAN_INTERVAL,
                    "scan_count": STATE["scan_count"],
                    "deltas": {k: round(v[1], 2) for k, v in gem_deltas().items()},
                }
            self._send(200, json.dumps(payload, ensure_ascii=False))
            return
        if path == "/api/scan":
            if STATE["scanning"]:
                self._send(200, json.dumps({"ok": False, "reason": "scanning"}))
                return
            if STATE["last_scan"] and time.time() - STATE["last_scan"] < MIN_MANUAL_GAP:
                left = int(MIN_MANUAL_GAP - (time.time() - STATE["last_scan"]))
                self._send(200, json.dumps({"ok": False, "reason": "cooldown", "wait": left}))
                return
            threading.Thread(target=run_scan, daemon=True).start()
            self._send(200, json.dumps({"ok": True, "started": True}))
            return
        if path == "/api/export.csv":
            body = deals_csv(STATE["rate"], STATE["deals"])
            self._send(200, "\ufeff" + body, "text/csv; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="dota_gem_deals.csv"'})
            return
        if path == "/api/health":
            self._send(200, json.dumps({"ok": True, "last_scan": STATE["last_scan"],
                                        "scanning": STATE["scanning"]}))
            return
        self._send(404, json.dumps({"ok": False, "error": "not found"}))


def main():
    global SCAN_INTERVAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--interval", type=int, default=SCAN_INTERVAL)
    ap.add_argument("--no-scan", action="store_true",
                    default=os.environ.get("NO_SCAN") == "1")
    args = ap.parse_args()
    SCAN_INTERVAL = args.interval

    if not args.no_scan:
        threading.Thread(target=bg_loop, args=(args.interval,), daemon=True).start()

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    log(f"http://0.0.0.0:{args.port}  интервал скана {args.interval}с")
    srv.serve_forever()


if __name__ == "__main__":
    main()
