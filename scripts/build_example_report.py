"""Build the published evolution report, with clearly marked sample token usage."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from honed.resources.evolution import evolution
from honed.resources.report import render_report

WORKSPACE = ROOT / "artifacts" / "evolution" / "graphify"
OUTPUT = ROOT / "docs" / "examples" / "report.html"


def main() -> None:
    data = evolution(WORKSPACE)
    data["usage_note"] = (
        "Illustrative token usage for the example only; these figures are synthetic and are not from the Graphify runs."
    )
    runs = []
    kinds = ("reflection", "curation", "merge")
    for round_number in range(1, 5):
        releases = [item for item in data["graph"]["releases"] if item["version"].endswith(f".{round_number + 68}")]
        release = releases[0] if releases else data["graph"]["releases"][min(round_number, len(data["graph"]["releases"])) - 1]
        for kind_index, kind in enumerate(kinds):
            input_tokens = 12000 + round_number * 1800 + kind_index * 3200
            output_tokens = 900 + round_number * 140 + kind_index * 210
            runs.append({
                "kind": kind,
                "id": f"sample-{kind}-{round_number}",
                "at": release["at"],
                "revision": release["version"],
                "harness": ("claude", "codex", "codex")[kind_index],
                "model": ("example-model", "example-model", "example-model")[kind_index],
                "usage": {"input": input_tokens, "cache_read": input_tokens // 3, "cache_write": 0,
                          "output": output_tokens, "reasoning": output_tokens // 4, "cost": None},
                "run": next((item["id"] for item in data["graph"]["runs"] if item["base_version"] == release["parent_version"]), None),
            })
    data["usage"] = runs
    OUTPUT.write_text(render_report(data), encoding="utf-8")


if __name__ == "__main__":
    main()
