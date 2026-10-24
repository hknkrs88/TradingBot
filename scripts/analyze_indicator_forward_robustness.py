"""G-O robustness of G-N forward-return associations. Local files only."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from tradingbot.research.robustness import run_robustness_study

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "experimental" / "g_o_forward_robustness.json"
G_N = ROOT / "data" / "experimental" / "g_n_indicator_forward.json"


def main() -> None:
    now_ms = int(time.time() * 1000)
    report = run_robustness_study(root=ROOT, now_ms=now_ms, g_n_path=G_N)
    report["generated_at"] = datetime.now(tz=UTC).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    slim = {key: value for key, value in report.items() if key != "monthly"}
    monthly_summaries = {
        name: {h: payload["summary"] for h, payload in by_h.items()}
        for name, by_h in report["monthly"].items()
    }
    slim["monthly_summaries"] = monthly_summaries
    print(json.dumps(slim, indent=2, ensure_ascii=False))
    print("wrote", OUT)
    print("preserved", G_N, "exists", G_N.exists())


if __name__ == "__main__":
    main()
