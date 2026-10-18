"""Walk the demo video script through the real, live UI in a browser.

The AppTest dry run verifies values; this verifies the actual rendered page -
real buttons, real Streamlit reruns, real pixels, and captures a screenshot at
each beat so the storyboard can be checked before recording.

Requires a headless Chrome started with --remote-debugging-port=9222:

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\
      --headless=new --disable-gpu --no-sandbox --hide-scrollbars \\
      --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-shot-profile about:blank

    python scripts/live_ui_walkthrough.py [url] [outdir]
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import urllib.request
from pathlib import Path

import websockets

TARGET_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8501"
OUTDIR = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/immunegraph-storyboard")
WIDTH, HEIGHT = 1900, 1250

METRICS_JS = """(() => {
  const out = {};
  document.querySelectorAll('[data-testid="stMetric"]').forEach(m => {
    const l = m.querySelector('[data-testid="stMetricLabel"]');
    const v = m.querySelector('[data-testid="stMetricValue"]');
    if (l && v) out[l.innerText.trim()] = v.innerText.trim();
  });
  return out;
})()"""

ALERTS_JS = """(() => [...document.querySelectorAll('[data-testid="stAlert"]')]
  .map(a => a.innerText.trim()))()"""


def click_js(label: str) -> str:
    return """(() => {
      const btns = [...document.querySelectorAll('button')];
      const b = btns.find(x => (x.innerText || '').includes(%s));
      if (!b) return 'NOT_FOUND';
      b.click();
      return 'CLICKED';
    })()""" % json.dumps(label)


async def run() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)

    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
    page = next((t for t in targets if t.get("type") == "page"), None)
    if page is None:
        print("no Chrome page target on :9222")
        return 1

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f", {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=300 * 1024 * 1024) as ws:
        counter = 0

        async def call(method: str, params: dict | None = None) -> dict:
            nonlocal counter
            counter += 1
            mid = counter
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(f"{method}: {msg['error']}")
                    return msg.get("result", {})

        async def js(expression: str):
            res = await call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
            return res.get("result", {}).get("value")

        async def shot(name: str) -> None:
            res = await call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            path = OUTDIR / f"{name}.png"
            path.write_bytes(base64.b64decode(res["data"]))
            print(f"       saved {path.name}")

        async def settle(seconds: float) -> None:
            await asyncio.sleep(seconds)

        await call("Page.enable")
        await call("Runtime.enable")
        await call(
            "Emulation.setDeviceMetricsOverride",
            {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 2, "mobile": False},
        )
        await call("Page.navigate", {"url": TARGET_URL})
        print("Waiting for the app to open its websocket and render ...")
        await settle(26)

        # ---------------------------------------------------------- beat 0
        print("\n=== BEAT 0, reset to a clean graph, cold open ===")
        print(f"  Reset graph -> {await js(click_js('Reset graph'))}")
        await settle(6)
        boot_metrics = await js(METRICS_JS)
        print(f"  metrics: {boot_metrics}")
        check("page rendered the dashboard", bool(boot_metrics), str(boot_metrics))
        check("peak severity is 0 on a clean graph", boot_metrics.get("Peak severity") in ("0.0", "0"))
        await shot("beat0-clean")

        # ---------------------------------------------------------- beat 1
        print("\n=== BEAT 1, click 'Run Mars mission' ===")
        print(f"  Run Mars mission -> {await js(click_js('Run Mars mission'))}")
        await settle(10)
        run_metrics = await js(METRICS_JS)
        print(f"  metrics: {run_metrics}")
        check("severity 6.0 as narrated", run_metrics.get("Peak severity") == "6.0", str(run_metrics))
        check("exactly one outbreak contained after the run",
              run_metrics.get("Contained") == "1", str(run_metrics))
        check("nothing left active", run_metrics.get("Active outbreaks") == "0", str(run_metrics))
        check("a graph canvas is actually drawn",
              bool(await js("!!document.querySelector('iframe[title=\"streamlit_agraph.agraph\"]')")))
        await shot("beat1-cascade-detected")

        # ---------------------------------------------------------- beat 2
        print("\n=== BEAT 2, click 'Re-inject the false claim' ===")
        print(f"  Re-inject -> {await js(click_js('Re-inject the false claim'))}")
        await settle(8)
        recall_metrics = await js(METRICS_JS)
        alerts = await js(ALERTS_JS) or []
        print(f"  metrics: {recall_metrics}")
        for a in alerts:
            print(f"  alert: {a[:110]}")
        check("immunity recall banner appeared",
              any("Immunity recall fired" in a for a in alerts), str(alerts)[:160])
        check("two outbreaks contained now", recall_metrics.get("Contained") == "2", str(recall_metrics))
        await shot("beat2-immunity-recall")

        print(f"\nStoryboard written to {OUTDIR}")
        if failures:
            print(f"LIVE WALKTHROUGH FAILED, {len(failures)}: {failures}")
            return 1
        print("LIVE WALKTHROUGH PASSED, the real UI does what the script says.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
