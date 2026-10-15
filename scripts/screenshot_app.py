"""Screenshot a running Streamlit app via the Chrome DevTools Protocol.

`--virtual-time-budget` cannot capture Streamlit: the page holds a websocket
open, so virtual time never settles and you get the skeleton loader. This
drives a real headless Chrome instead, waits in real time, then captures.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import urllib.request

import websockets

TARGET_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8501"
OUTPUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/immunegraph.png"
WAIT_SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 28.0
WIDTH, HEIGHT = 1900, 1250


async def main() -> int:
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
    page = next((t for t in targets if t.get("type") == "page"), None)
    if page is None:
        print("no page target found", file=sys.stderr)
        return 1

    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=200 * 1024 * 1024) as ws:
        counter = 0

        async def call(method: str, params: dict | None = None) -> dict:
            nonlocal counter
            counter += 1
            message_id = counter
            await ws.send(json.dumps({"id": message_id, "method": method, "params": params or {}}))
            while True:
                message = json.loads(await ws.recv())
                if message.get("id") == message_id:
                    if "error" in message:
                        raise RuntimeError(f"{method}: {message['error']}")
                    return message.get("result", {})

        await call("Page.enable")
        await call(
            "Emulation.setDeviceMetricsOverride",
            {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 2, "mobile": False},
        )
        await call("Page.navigate", {"url": TARGET_URL})

        # Streamlit needs real wall-clock time to open its websocket, run the
        # script and stream the delta to the client.
        for _ in range(int(WAIT_SECONDS * 2)):
            await asyncio.sleep(0.5)

        result = await call(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True},
        )
        payload = base64.b64decode(result["data"])
        with open(OUTPUT, "wb") as handle:
            handle.write(payload)
        print(f"saved {OUTPUT} ({len(payload) / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
