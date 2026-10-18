"""Record the demo as a real video, by driving the live UI and capturing frames.

This exists so the project has an actual demo video (rule 10) without needing a
person at a screen recorder. It drives the running app through the beats in
docs/DEMO_SCRIPT.md over the Chrome DevTools Protocol, captures the page as it
changes, burns in a caption bar so the result is self-explanatory without audio,
and encodes an H.264 MP4.

Frames are written to disk and kept, so encoding can be iterated on without
re-recording (which needs a live browser and takes a couple of minutes):

    python scripts/record_demo.py /tmp/immunegraph-demo/demo.mp4
    python scripts/record_demo.py /tmp/immunegraph-demo/demo.mp4 --reuse

Requires headless Chrome on :9222, a running app, and ffmpeg. ffmpeg comes from
the optional `imageio-ffmpeg` package, or from PATH if you already have one.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import websockets

URL = "http://127.0.0.1:8501"
WIDTH, HEIGHT = 1680, 1200
FPS = 10
JPEG_QUALITY = 78

OUT = Path("/tmp/immunegraph-demo/demo.mp4")
FRAMES_DIR = Path("/tmp/immunegraph-demo/frames")

@dataclass
class Beat:
    """One narrated moment in the video."""

    caption: str
    hold: float
    button: str | None = None
    # Outline a panel so the eye is told where to look. Between beats 4 and 6 the
    # app state is static (the mission click does the whole arc), so without this
    # the middle of the video is a still image with changing captions.
    highlight: str | None = None
    highlight_index: int | None = None
    # Switch to a tab before acting. The app is multi-tab now, and a click on a
    # button inside an inactive tab still fires even though nobody can see it.
    tab: str | None = None


# One Re-inject click, deliberately. Each click is a fresh offence and creates
# its own repeat_offender infection, so clicking twice inflates the dashboard to
# "Contained 3" and muddies the closing beat. The fingerprint (exact-match) path
# is described in a caption instead of being demonstrated.
BEATS: list[Beat] = [
    Beat(
        "ImmuneGraph — a FalkorDB-backed immune system for AI agent swarms. "
        "Every claim, source, agent and derivation is a node in FalkorDB.",
        hold=7.0,
    ),
    Beat(
        "Three agents are about to plan a Mars mission. The graph is empty. "
        "Everything you are about to see is a real Cypher query against a real instance.",
        hold=5.0,
        highlight='[data-testid="stMetric"]',
    ),
    Beat(
        "The Researcher asserts a claim with no source. The other two reason CORRECTLY "
        "from it — that is the failure mode this project exists to catch.",
        hold=5.0,
        button="Run Mars mission",
    ),
    Beat(
        "Q2 finds the unsupported root. Q4 computes SEVERITY FROM GRAPH REACH: "
        "1 root + 2 contaminated claims + 3 exposed agents = 6.0. Not an LLM's opinion.",
        hold=8.0,
        highlight='[data-testid="stMetric"]',
        highlight_index=2,
    ),
    Beat(
        "Q4's traversal also gives the blast radius: spread 2. An antibody injects a "
        "sourced correction citing NASA and quarantines the root claim.",
        hold=7.0,
        # Scoped to stMain: a bare stExpander matches the sidebar's expanders first.
        highlight='[data-testid="stMain"] [data-testid="stExpander"]',
    ),
    Beat(
        "The derived claims are downgraded to 'support invalidated' — amber, not red. "
        "They were never proven false; their justification was refuted.",
        hold=6.0,
        highlight='[data-testid="stChatMessage"]',
    ),
    Beat(
        "And because the correction cited a source, it became a KERNEL FACT: what the "
        "swarm now holds as TRUE, with the antibody that established it and the source "
        "behind it. The swarm has stopped only remembering its mistakes.",
        hold=10.0,
        highlight='[data-testid="stMain"] [data-testid="stDataFrame"]',
        highlight_index=2,
    ),
    Beat(
        "Trust is DERIVED from the graph, never stored (Q5). All three fall to 0.85 — the "
        "Planner never touched the lie, it inherited it through the derivation chain.",
        hold=7.0,
        highlight='[data-testid="stMain"] [data-testid="stDataFrame"]',
        highlight_index=0,
    ),
    Beat(
        "Now the Researcher repeats the claim — as a paraphrase, not the same words.",
        hold=4.0,
        button="Re-inject the false claim",
    ),
    Beat(
        "The sanitiser catches it against the kernel in about five milliseconds — no "
        "extraction, no cascade scan. Compare that with the three seconds the original "
        "detection needed. The re-offending Researcher drops to 0.70.",
        hold=11.0,
        highlight='[data-testid="stAlert"]',
    ),
    Beat(
        "Every claim so far was scripted. This one is not — anyone can submit a claim "
        "the swarm has never seen.",
        hold=6.0,
        tab="Check any claim",
    ),
    Beat(
        "A false statement a judge can type themselves. It goes through exactly the same "
        "machinery as the Mars cascade.",
        hold=5.0,
        button="Venus has a breathable atmosphere",
    ),
    Beat(
        "GROUNDED AGAINST WIKIPEDIA — free, public and citable, so no API key and no private "
        "data. Watch the infection spread: the Analyst and Planner genuinely derive from it, "
        "so severity climbs 2.0 -> 4.0 -> 6.0. Those numbers are Q4, computed after each write.",
        hold=14.0,
        button="Check this claim",
    ),
    Beat(
        "Refuted, quarantined, and the correcting fact enters the KERNEL — so a repeat of this "
        "claim is caught with no network call at all. Same infection, same antibody, same "
        "kernel. Only the source of truth changed.",
        hold=9.0,
    ),
    Beat(
        "TRACK 03 — a company brain. Four synthetic documents load as Source nodes; the facts "
        "they establish become the kernel of truth, each linked to the document it came from.",
        hold=9.0,
        tab="Company brain",
    ),
    Beat(
        "A department agent invents a price. Support repeats it — with a real DERIVED_FROM edge "
        "back to Sales, so this is a cascade and not two coincidences. Severity 4.0, from Q4.",
        hold=14.0,
        button="Ask the company brain",
    ),
    Beat(
        "Then the price actually changes. The old fact is SUPERSEDED, not deleted, and the "
        "answer that was correct last week is caught in 89ms. A brain that cannot forget is as "
        "dangerous as one that cannot learn.",
        hold=11.0,
    ),
    Beat(
        "Every system in this space reports what it CATCHES. That number is gameable: a detector "
        "that flags everything scores 100%. So we measure the other side too.",
        hold=10.0,
        tab="Self-audit",
    ),
    Beat(
        "Three numbers, never one. Coverage, autoimmunity — the rate at which it wrongly flags "
        "TRUE statements — and attribution: did it fire at the right fact, or just at one?",
        hold=13.0,
        button="Run self-audit",
    ),
    Beat(
        "The audit finds five falsehoods that NO fact contradicts — 'a Starter plan at the "
        "Enterprise price'. Comparison can never catch those. So we record them as known-false "
        "and re-measure: holes closed, autoimmunity still zero.",
        hold=15.0,
        button="Vaccinate blind spots",
    ),
    Beat(
        "That pair earned its place on our own code. With 134 tests green it measured 20% "
        "autoimmunity, we fixed it to zero — and the obvious repair drove it to 82% while "
        "coverage still read 100%. One number would have shipped that as a success.",
        hold=13.0,
    ),
    Beat(
        "The kernel of truth: what the swarm holds as TRUE, with the antibody that established "
        "each fact and the source behind it. Opposite in kind to Infection, which records "
        "what was wrong.",
        hold=9.0,
        tab="Kernel & durability",
    ),
    Beat(
        "FalkorDB Cloud's free tier has no persistence or backups, so every write is logged "
        "BEFORE it is applied. Now wipe the graph and rebuild it from that log.",
        hold=8.0,
        button="Rebuild from log",
    ),
    Beat(
        "73 writes replayed, 0 failures, and the result is identical — including node ids. "
        "That is why the log stores whole statements rather than semantic events: replaying "
        "a statement reproduces its generated id exactly.",
        hold=11.0,
    ),
    Beat(
        "Q8: both outbreak types contained, none recurred, and one fast-path recognition "
        "recorded. Detection finds lies the swarm has never seen; the kernel recognises "
        "lies it already knows. That difference is what immunity means here.",
        hold=10.0,
        highlight='[data-testid="stMain"] [data-testid="stDataFrame"]',
        highlight_index=1,
    ),
]


def click_js(label: str) -> str:
    return """(() => {
      const b = [...document.querySelectorAll('button')]
        .find(x => (x.innerText || '').includes(%s));
      if (!b) return 'NOT_FOUND';
      b.click();
      return 'CLICKED';
    })()""" % json.dumps(label)


def tab_js(label: str) -> str:
    return """(() => {
      const t = [...document.querySelectorAll('[role="tab"]')]
        .find(x => (x.innerText || '').includes(%s));
      if (!t) return 'NOT_FOUND';
      t.click();
      return 'SWITCHED';
    })()""" % json.dumps(label)


def caption_js(text: str) -> str:
    return """(() => {
      let el = document.getElementById('__ig_caption');
      if (!el) {
        el = document.createElement('div');
        el.id = '__ig_caption';
        el.style.cssText = [
          'position:fixed','left:0','right:0','bottom:0','z-index:2147483647',
          'padding:16px 26px','background:rgba(12,15,22,.94)','color:#fff',
          'font:600 20px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
          'letter-spacing:.2px','border-top:4px solid #E8453C','box-sizing:border-box'
        ].join(';');
        document.body.appendChild(el);
      }
      el.textContent = %s;
      return 'ok';
    })()""" % json.dumps(text)


def highlight_js(selector: str | None, index: int | None) -> str:
    """Outline a panel, clearing whatever was outlined before.

    Without this the middle of the video is a still image: one click of "Run
    Mars mission" performs the whole arc, so beats 4 to 6 show identical pixels
    with different captions.
    """
    if not selector:
        return """(() => {
          document.querySelectorAll('.__ig_hl').forEach(e => {
            e.style.outline = ''; e.style.outlineOffset = '';
            e.classList.remove('__ig_hl');
          });
          return 'cleared';
        })()"""
    target = json.dumps(None if index is None else index)
    return """(() => {
      document.querySelectorAll('.__ig_hl').forEach(e => {
        e.style.outline = ''; e.style.outlineOffset = '';
        e.classList.remove('__ig_hl');
      });
      const all = [...document.querySelectorAll(%s)];
      const idx = %s;
      const chosen = (idx === null) ? all : (all[idx] ? [all[idx]] : []);
      if (!chosen.length) return 'NOT_FOUND';
      chosen.forEach(e => {
        e.style.transition = 'outline .2s ease';
        e.style.outline = '3px solid #E8453C';
        e.style.outlineOffset = '8px';
        e.style.borderRadius = '10px';
        e.classList.add('__ig_hl');
      });
      // The dashboard is taller than the viewport now that it carries the kernel
      // panel, so outlining an element is useless if the viewer cannot see it.
      chosen[0].scrollIntoView({block: 'center', inline: 'nearest'});
      return 'OK';
    })()""" % (json.dumps(selector), target)


class CDP:
    """Minimal DevTools Protocol client that can also receive events.

    A naive request/response client reads until it finds its own reply and
    throws away everything else, which loses screencast frames.
    """

    def __init__(self, ws) -> None:
        self.ws = ws
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, object] = {}
        self._reader: asyncio.Task | None = None

    async def start(self) -> None:
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                if "id" in message:
                    future = self._pending.pop(message["id"], None)
                    if future and not future.done():
                        future.set_result(message)
                elif "method" in message:
                    handler = self._handlers.get(message["method"])
                    if handler:
                        asyncio.create_task(handler(message.get("params", {})))
        finally:
            # If the socket dies (Chrome crashed, tab closed), every in-flight
            # request must be failed. Otherwise callers await a future that can
            # never resolve and the script hangs forever instead of reporting.
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Chrome DevTools connection closed"))
            self._pending.clear()

    def on(self, method: str, handler) -> None:
        self._handlers[method] = handler

    async def call(self, method: str, params: dict | None = None, timeout: float = 45.0) -> dict:
        self._id += 1
        request_id = self._id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self.ws.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        try:
            message = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise RuntimeError(f"{method}: no response after {timeout:.0f}s") from None
        if "error" in message:
            raise RuntimeError(f"{method}: {message['error']}")
        return message.get("result", {})

    async def js(self, expression: str):
        result = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return result.get("result", {}).get("value")


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        found = shutil.which("ffmpeg")
        if not found:
            raise SystemExit(
                "No ffmpeg. Install it with:  pip install imageio-ffmpeg\n"
                "or put a system ffmpeg on PATH."
            )
        return found


# --------------------------------------------------------------------- record


async def wait_for_app(cdp: "CDP", timeout: float = 90.0) -> bool:
    """Block until the app has actually rendered, rather than sleeping blindly.

    A fixed sleep must cover the worst case, which means Chrome idles on a static
    page for most of it — and idle headless Chrome on this machine has been
    observed to exit by itself. Polling starts the recording the moment the
    dashboard exists and keeps that window of exposure short.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await cdp.js("!!document.querySelector('[data-testid=\"stMetric\"]')"):
                return True
        except Exception:  # noqa: BLE001 - the connection may still be settling
            pass
        await asyncio.sleep(0.5)
    return False


async def record(frames_dir: Path) -> int:
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:9222/json"))
    page = next((t for t in targets if t.get("type") == "page"), None)
    if page is None:
        raise SystemExit("no Chrome page target on :9222")

    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("src_*.jpg"):
        stale.unlink()

    # Clean cold open. The app renders whatever is already in the graph, so a
    # previous run would otherwise appear behind the opening caption.
    from immunegraph.config import Settings
    from immunegraph.db import GraphStore

    store = GraphStore(Settings.from_env())
    if not store.ping():
        raise SystemExit("FalkorDB unreachable — start it with: docker compose up -d")
    store.reset()
    store.create_indexes()
    print("graph reset for a clean cold open", flush=True)

    timestamps: list[float] = []

    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=400 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.start()

        async def on_frame(params: dict) -> None:
            index = len(timestamps)
            timestamps.append(params["metadata"]["timestamp"])
            (frames_dir / f"src_{index:05d}.jpg").write_bytes(
                base64.b64decode(params["data"])
            )
            await cdp.call("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)

        await cdp.call("Page.enable")
        await cdp.call("Runtime.enable")
        await cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {"width": WIDTH, "height": HEIGHT, "deviceScaleFactor": 1, "mobile": False},
        )
        await cdp.call("Page.navigate", {"url": URL})
        print("Waiting for the app to render ...", flush=True)
        if not await wait_for_app(cdp):
            raise RuntimeError("the app never rendered a dashboard within 90s")
        print("App is up.", flush=True)

        await cdp.call(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": JPEG_QUALITY,
                "maxWidth": WIDTH,
                "maxHeight": HEIGHT,
                "everyNthFrame": 1,
            },
        )
        print("Recording ...", flush=True)

        for index, beat in enumerate(BEATS, start=1):
            if beat.tab:
                switched = await cdp.js(tab_js(beat.tab))
                print(f"  tab -> {beat.tab}: {switched}", flush=True)
                await asyncio.sleep(2.0)
            await cdp.js(caption_js(beat.caption))
            await cdp.js(highlight_js(beat.highlight, beat.highlight_index))
            action = ""
            if beat.button:
                action = f"  [{beat.button} -> {await cdp.js(click_js(beat.button))}]"
            mark = "  [highlight]" if beat.highlight else ""
            print(f"  beat {index}/{len(BEATS)}  {beat.hold:4.1f}s{action}{mark}", flush=True)
            remaining = beat.hold
            while remaining > 0:
                await asyncio.sleep(min(0.25, remaining))
                remaining -= 0.25

        await cdp.call("Page.stopScreencast")

    (frames_dir / "timestamps.json").write_text(json.dumps(timestamps), encoding="utf-8")
    return len(timestamps)


# --------------------------------------------------------------------- encode


def encode(frames_dir: Path, out: Path) -> None:
    """Encode captured frames to MP4 at a constant frame rate.

    Uses the image2 demuxer with an explicit input framerate. The concat
    demuxer is a trap here: given bare image files it reads them at its default
    25fps, so an output `-r 10` silently resamples the whole video down to 40%
    of its intended duration.
    """
    timestamps = json.loads((frames_dir / "timestamps.json").read_text(encoding="utf-8"))
    sources = sorted(frames_dir.glob("src_*.jpg"))
    if len(sources) < 2:
        raise SystemExit(f"only {len(sources)} frames in {frames_dir}")

    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(f"{frames_dir}/seq")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    # The screencast is change-driven, so static holds produce no frames. Repeat
    # each frame for its gap to the next one. The cap must exceed the longest
    # caption hold, or the video runs short and cuts captions off.
    max_hold = int(FPS * 15)
    position = 1
    for index, source in enumerate(sources):
        if index + 1 < len(sources):
            duration = max(0.02, timestamps[index + 1] - timestamps[index])
        else:
            duration = 1.0
        repeats = max(1, min(int(math.ceil(duration * FPS)), max_hold))
        for _ in range(repeats):
            target = work / f"frame_{position:05d}.jpg"
            try:
                os.link(source, target)  # free: same inode, separate name
            except OSError:
                shutil.copyfile(source, target)
            position += 1

    total = position - 1
    print(
        f"Encoding {total} frames ({total / FPS:.1f}s at {FPS}fps) from "
        f"{len(sources)} captures -> {out} ...",
        flush=True,
    )
    subprocess.run(
        [
            ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(FPS),
            "-i", "frame_%05d.jpg",
            "-c:v", "libx264", "-preset", "medium", "-crf", "26",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(out),
        ],
        check=True,
        cwd=work,
    )


def report(path: Path) -> float:
    probe = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True
    )
    duration = 0.0
    for line in probe.stderr.splitlines():
        if "Duration" in line:
            stamp = line.split("Duration:")[1].split(",")[0].strip()
            hours, minutes, seconds = stamp.split(":")
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            print("  " + line.strip())
        elif "Stream #" in line:
            print("  " + line.strip()[:150])
    return duration


# ----------------------------------------------------------------------- main


def preflight() -> None:
    """Fail fast and loudly rather than hanging mid-recording."""
    try:
        with urllib.request.urlopen(f"{URL}/_stcore/health", timeout=8) as response:
            if response.read().strip() != b"ok":
                raise SystemExit(f"app at {URL} is not healthy")
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"cannot reach the app at {URL} ({exc}).\nStart it with:  streamlit run app.py"
        ) from None

    try:
        json.load(urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=8))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"cannot reach Chrome DevTools on :9222 ({exc}).\n"
            '  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \\\n'
            "    --headless=new --disable-gpu --no-sandbox --hide-scrollbars \\\n"
            "    --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-shot-profile about:blank"
        ) from None

    ffmpeg_exe()
    print(f"preflight ok — app {URL} healthy, DevTools on :9222, ffmpeg available", flush=True)


def main() -> int:
    # Line-buffer so progress is visible when stdout is a pipe (a background job,
    # for example). Block buffering hides everything until the process exits.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:  # noqa: BLE001
        pass

    out = OUT
    reuse = "--reuse" in sys.argv
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if positional:
        out = Path(positional[0])

    if not reuse:
        preflight()
        try:
            count = asyncio.run(record(FRAMES_DIR))
            print(f"\nCaptured {count} frames into {FRAMES_DIR}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"\nrecording aborted: {type(exc).__name__}: {exc}", flush=True)
            if not (FRAMES_DIR / "timestamps.json").exists():
                return 1
            print("encoding whatever was captured before the failure", flush=True)

    encode(FRAMES_DIR, out)
    print(f"\nWrote {out}  ({out.stat().st_size / 1_048_576:.1f} MB)")
    duration = report(out)
    print(f"\nFinal duration: {duration:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
