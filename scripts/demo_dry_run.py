"""Dry-run the demo video script against the live app.

docs/DEMO_SCRIPT.md makes specific claims about what is on screen at each beat
("severity six point zero", "all three agents drop to zero point eight five").
This walks those beats through Streamlit's AppTest harness and checks each claim
against the rendered state, timing every step so the narration can be paced.

Run it before recording. It exits non-zero if any beat does not do what the
script says it does.

    python scripts/demo_dry_run.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "app.py"

# Populated by main() so it works whether or not the package is importable.
sys.path.insert(0, str(REPO_ROOT / "src"))
from immunegraph.config import Settings  # noqa: E402
from immunegraph.db import GraphStore  # noqa: E402

PASS, FAIL, INFO = "PASS", "FAIL", "  .."
failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    tag = PASS if condition else FAIL
    print(f"  [{tag}] {label}" + (f", {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def button_index(at, needle: str) -> int:
    return next(i for i, b in enumerate(at.button) if needle in b.label)


def metrics(at) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def find_frame(at, column: str):
    """Locate a rendered dataframe by one of its columns, not by position."""
    for element in at.dataframe:
        frame = element.value
        if column in getattr(frame, "columns", []):
            return frame
    return None


def trust_map(at) -> dict[str, float]:
    frame = find_frame(at, "effective")
    if frame is None:
        return {}
    return {row["agent"]: float(row["effective"]) for _, row in frame.iterrows()}


def effectiveness_map(at) -> dict[str, tuple[int, int, int]]:
    frame = find_frame(at, "infection_type")
    if frame is None:
        return {}
    return {
        row["infection_type"]: (int(row["outbreaks"]), int(row["contained"]), int(row["recurred"]))
        for _, row in frame.iterrows()
    }


def main() -> int:
    store = GraphStore(Settings.from_env())
    if not store.ping():
        print("FalkorDB is unreachable. Start it with: docker compose up -d")
        return 2

    print("Resetting the graph for a clean take ...")
    store.reset()
    store.create_indexes()
    st.cache_resource.clear()

    print("\n=== BEAT 0 (0:00), cold open, empty graph ===")
    started = time.time()
    at = AppTest.from_file(str(APP_PATH), default_timeout=240)
    at.run()
    print(f"  app boot: {time.time() - started:.1f}s")
    check("renders without exception", not at.exception, str(at.exception)[:120])
    check(
        "shows the empty-state line the script references",
        any("No conversation yet" in (c.value or "") for c in at.caption),
    )
    check("Peak severity is 0 before anything happens", metrics(at).get("Peak severity") == "0.0",
          f"got {metrics(at).get('Peak severity')}")

    print("\n=== BEAT 1 (0:35), Act I: click 'Run Mars mission' ===")
    started = time.time()
    at.button[button_index(at, "Run Mars")].click().run()
    elapsed = time.time() - started
    print(f"  cascade + detection + antibody: {elapsed:.1f}s on screen")
    check("no exception", not at.exception, str(at.exception)[:120])
    check("a risk: the run takes under ~6s, so narration must fill the gap",
          elapsed < 12, f"{elapsed:.1f}s")

    print("\n=== BEAT 2 (1:15), Act II/III: the dashboard after the run ===")
    m = metrics(at)
    print(f"  on screen: Active={m.get('Active outbreaks')} Contained={m.get('Contained')} "
          f"Peak severity={m.get('Peak severity')}")
    check("peak severity is 6.0 exactly as narrated", m.get("Peak severity") == "6.0",
          f"got {m.get('Peak severity')}")
    check("nothing is still active (the antibody contained it)", m.get("Active outbreaks") == "0")
    check("one outbreak contained at this point, not two", m.get("Contained") == "1",
          f"got {m.get('Contained')}")

    trust = trust_map(at)
    print(f"  trust: {trust}")
    check("all three planning agents are at 0.85",
          all(abs(trust.get(a, -1) - 0.85) < 1e-6 for a in ("Researcher", "Analyst", "Planner")),
          str(trust))
    check("the script's 'all three drop to 0.85' claim holds", len(trust) == 4)

    print("\n=== BEAT 3 (2:20), Act V: click 'Re-inject the false claim' ===")
    started = time.time()
    at.button[button_index(at, "Re-inject")].click().run()
    print(f"  recall response: {time.time() - started:.1f}s")
    check("no exception", not at.exception, str(at.exception)[:120])
    banners = [s.value or "" for s in at.success]
    check("immunity recall fired", any("Immunity recall fired" in b for b in banners), str(banners))
    check("the banner names the paraphrase path", any("similarity" in b for b in banners), str(banners))

    print("\n=== BEAT 4 (2:45), close: did it work? ===")
    m = metrics(at)
    print(f"  on screen: Active={m.get('Active outbreaks')} Contained={m.get('Contained')} "
          f"Peak severity={m.get('Peak severity')}")
    check("two outbreaks contained after the re-injection", m.get("Contained") == "2",
          f"got {m.get('Contained')}")

    trust = trust_map(at)
    print(f"  trust: {trust}")
    check("the re-offending Researcher drops to 0.70",
          abs(trust.get("Researcher", -1) - 0.70) < 1e-6, str(trust))
    check("the other two stay at 0.85",
          abs(trust.get("Analyst", -1) - 0.85) < 1e-6 and abs(trust.get("Planner", -1) - 0.85) < 1e-6)

    eff = effectiveness_map(at)
    print(f"  immunity memory: {eff}")
    check("unsupported_root: 1 outbreak, contained, none recurred",
          eff.get("unsupported_root") == (1, 1, 0), str(eff.get("unsupported_root")))
    check("repeat_offender: 1 outbreak, contained, none recurred",
          eff.get("repeat_offender") == (1, 1, 0), str(eff.get("repeat_offender")))

    print("\n=== BEAT 5, optional second re-inject (exact-match path) ===")
    at.button[button_index(at, "Re-inject")].click().run()
    check("no exception on the second re-injection", not at.exception, str(at.exception)[:120])
    banners = [s.value or "" for s in at.success]
    check("falls back to the fingerprint (exact) path",
          any("exact match" in b for b in banners), str(banners))

    print()
    if failures:
        print(f"DRY RUN FAILED, {len(failures)} check(s): {failures}")
        return 1
    print("DRY RUN PASSED, every beat does what DEMO_SCRIPT.md says it does.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
