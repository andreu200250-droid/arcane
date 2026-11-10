#!/usr/bin/env python3
"""
Headless-скан без запуска сервера: удобно гонять из GitHub Actions по расписанию.

    python3 scripts/snapshot.py

Пишет:
  data/deals.csv        — все лоты с гемом и выгодами (UTF-8 BOM, разделитель ';')
  $GITHUB_STEP_SUMMARY  — человекочитаемый топ-10 в summary запуска workflow
"""
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("DB_PATH", str(REPO / "monitor.db"))

import app  # noqa: E402


def main() -> int:
    app.run_scan()
    if app.STATE["last_error"]:
        print("Скан не удался:", app.STATE["last_error"], file=sys.stderr)
        return 1

    rate, deals = app.STATE["rate"], app.STATE["deals"]
    out = REPO / "data"
    out.mkdir(exist_ok=True)
    csv_path = out / "deals.csv"
    csv_path.write_text(app.deals_csv(rate, deals), encoding="utf-8-sig")
    print(f"{csv_path} — {len(deals)} лотов, курс {rate} ₽/$")

    top = [d for d in deals if not d["is_default"]][:10]
    lines = [
        "## Аркана + самоцвет: топ-10 по выгоде",
        "",
        "| Аркана | Самоцвет | Лот | Гем отдельно | Собрать самому | Выгода |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for d in top:
        lines.append(
            f"| {d['hero']} | {d['gem']} | {round(d['price_usd'] * rate)} ₽ | "
            f"{round(d['gem_usd'] * rate)} ₽ | {round(d['set_usd'] * rate)} ₽ | "
            f"**+{round(d['save_usd'] * rate)} ₽ ({d['save_pct']}%)** |"
        )
    lines += ["", f"Всего лотов с гемом: {len(deals)} · курс 1$ = {rate} ₽"]
    table = "\n".join(lines)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(table + "\n")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
