"""G-N local 4h indicator vs forward-return study. No sync, orders, or apply."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from tradingbot.research.indicator_assoc import run_universe_study

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "experimental" / "g_n_indicator_forward.json"


def main() -> None:
    now_ms = int(time.time() * 1000)
    report = run_universe_study(root=ROOT, now_ms=now_ms)
    report["generated_at"] = datetime.now(tz=UTC).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    slim = {key: value for key, value in report.items() if key != "per_symbol"}
    print(json.dumps(slim, indent=2, ensure_ascii=False))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
