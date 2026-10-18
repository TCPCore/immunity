# Demo video script and shot list

The video is generated, not hand-edited. `scripts/record_demo.py` drives the running app over the
Chrome DevTools Protocol, burns in a caption bar, outlines the relevant panel on each beat, and
encodes H.264. `scripts/demo_dry_run.py` asserts that every number a caption quotes is the number
that actually appears at that beat.

```bash
docker compose up -d
streamlit run app.py          # in one terminal
python scripts/record_demo.py # in another, with Chrome on --remote-debugging-port=9222
```

The current cut is published at <https://www.youtube.com/watch?v=kX1Jscw91so>.

## The 25 beats, with real timings

Timings are for the current cut (**4:17**, 1680x1200, 10 fps, silent with burned-in captions).
Beat boundaries are computed from the recorder's own hold times, scaled to the measured duration.

| # | starts | caption and action |
| --- | --- | --- |
| 1 | 0:00 | ImmuneGraph, a FalkorDB-backed immune system for AI agent swarms. Every claim, source, agent and derivation is a |
| 2 | 0:07 | Three agents are about to plan a Mars mission. The graph is empty. Everything you are about to see is a real Cyp |
| 3 | 0:13 | The Researcher asserts a claim with no source. The other two reason CORRECTLY from it, that is the failure mode  `Run Mars mission` |
| 4 | 0:18 | Q2 finds the unsupported root. Q4 computes SEVERITY FROM GRAPH REACH: 1 root + 2 contaminated claims + 3 exposed |
| 5 | 0:27 | Q4's traversal also gives the blast radius: spread 2. An antibody injects a sourced correction citing NASA and q |
| 6 | 0:34 | The derived claims are downgraded to 'support invalidated', amber, not red. They were never proven false; their  |
| 7 | 0:41 | And because the correction cited a source, it became a KERNEL FACT: what the swarm now holds as TRUE, with the a |
| 8 | 0:52 | Trust is DERIVED from the graph, never stored (Q5). All three fall to 0.85, the Planner never touched the lie, i |
| 9 | 1:00 | Now the Researcher repeats the claim, as a paraphrase, not the same words. `Re-inject the false claim` |
| 10 | 1:04 | The sanitiser catches it against the kernel in about five milliseconds, no extraction, no cascade scan. Compare  |
| 11 | 1:16 | Every claim so far was scripted. This one is not, anyone can submit a claim the swarm has never seen. switch tab |
| 12 | 1:25 | A false statement a judge can type themselves. It goes through exactly the same machinery as the Mars cascade. `Venus has a breathable atmosphere` |
| 13 | 1:30 | GROUNDED AGAINST WIKIPEDIA, free, public and citable, so no API key and no private data. Watch the infection spr `Check this claim` |
| 14 | 1:46 | Refuted, quarantined, and the correcting fact enters the KERNEL, so a repeat of this claim is caught with no net |
| 15 | 1:55 | TRACK 03, a company brain. Four synthetic documents load as Source nodes; the facts they establish become the ke switch tab |
| 16 | 2:08 | A department agent invents a price. Support repeats it, with a real DERIVED_FROM edge back to Sales, so this is  `Ask the company brain` |
| 17 | 2:23 | Then the price actually changes. The old fact is SUPERSEDED, not deleted, and the answer that was correct last w |
| 18 | 2:35 | Every system in this space reports what it CATCHES. That number is gameable: a detector that flags everything sc switch tab |
| 19 | 2:48 | Five numbers, never one. Precision and recall on a labelled set, F1, autoimmunity (the rate at which it wrongly  `Run self-audit` |
| 20 | 3:03 | The audit finds five falsehoods that NO fact contradicts, 'a Starter plan at the Enterprise price'. Comparison c `Vaccinate blind spots` |
| 21 | 3:19 | That pair earned its place on our own code. With the whole suite green it measured 20% autoimmunity, we fixed it |
| 22 | 3:33 | The kernel of truth: what the swarm holds as TRUE, with the antibody that established each fact and the source b switch tab |
| 23 | 3:45 | FalkorDB Cloud's free tier has no persistence or backups, so every write is logged BEFORE it is applied. Now wip `Rebuild from log` |
| 24 | 3:54 | 73 writes replayed, 0 failures, and the result is identical, including node ids. That is why the log stores whol |
| 25 | 4:06 | Q8: both outbreak types contained, none recurred, and one fast-path recognition recorded. Detection finds lies t |

## What each act is for

- **1 to 10, the scripted cascade.** Detection, severity from reach, the antibody, the kernel, and
  derived trust. This is the mechanism, complete.
- **11 to 14, a claim the video did not choose.** A visitor's statement is grounded against
  Wikipedia and arrested by the same machinery. This exists because a scripted demo only proves
  the mechanism works on inputs the author selected.
- **15 to 17, a company brain.** Documents become the kernel; a wrong price spreads between
  departments and is caught; a policy change supersedes the old fact.
- **18 to 21, the self-audit.** The one number every system reports is gameable, so this measures
  the other side. Precision, recall, F1, autoimmunity and attribution on a labelled set, then the
  vaccination loop that closes the blind spots it finds.
- **22 to 24, durability.** The graph is a projection rebuilt from a write-ahead log, and the
  rebuild is proven identical including node ids.
- **25, the payoff.** Q8: detection finds new lies, the kernel recognises known ones.

## Reproducing it

The recorder resets the graph first so the cold open is genuinely empty. Two things have broken
this in practice and are worth knowing:

- **Idle headless Chrome exits by itself.** The recorder polls for the app rather than sleeping a
  fixed time, and `wait_for_app()` fails loudly rather than recording a black screen.
- **The screencast API is not reliable here.** Chrome closes the socket mid-take with a 1011
  internal error and the run dies after several minutes. Capture now polls
  `Page.captureScreenshot` instead, which is slower but deterministic and fails loudly rather than
  leaving a truncated file.

- **A busy machine produces a slideshow.** One take captured 143 frames over 201 seconds. Check the
  frame count in the output; it should be several hundred for a take of this length.

`scripts/screenshot_app.py` takes a single still if you want a thumbnail without a full take.
