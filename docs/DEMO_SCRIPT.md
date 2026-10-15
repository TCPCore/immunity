# Demo video script

Target: **~3:00**. Rule 10 requires a demo video showing the working project, so this is
scripted beat by beat.

The goal is that a judge who watches only the video understands that **the graph is the
immune system**, not a visualisation bolted onto storage.

## Measured timings (dry-run on the real UI)

| Beat | On screen | Scripted slot |
| --- | --- | --- |
| App boot | 1.0s | — |
| "Run Mars mission" cascade + detection + antibody | **3.1s** | 0:35 – 1:15 (40s) |
| "Re-inject the false claim" recall | **0.2s** | 2:20 – 2:45 (25s) |

**The one pacing trap:** the cascade animation finishes in about three seconds, but Act I is
scripted as forty seconds of narration. Do not click and then start talking — you will be
pointing at a static graph for 37 seconds. Either narrate *what is about to happen* before
clicking, or click first and use the finished graph as the illustration while you explain the
three hops. Both work; the second looks better on camera.

The recall at the end is effectively instant, which is the point — say so rather than waiting.

---

## Pre-flight (do this before you hit record)

```bash
docker compose up -d
.venv/bin/python -m immunegraph.verify_queries    # must end with "All non-optional queries..."
.venv/bin/python scripts/demo_dry_run.py          # checks every beat below against the app
.venv/bin/streamlit run app.py

# Optional: drive the real browser through all three beats and save a storyboard
python scripts/live_ui_walkthrough.py http://127.0.0.1:8501 /tmp/storyboard
```

`demo_dry_run.py` asserts that the numbers this script quotes are the numbers that actually
appear at each beat. Run it first; if it passes, the recording should go smoothly.

- [ ] FalkorDB healthy: `docker ps` shows `immunegraph-db  Up ... (healthy)`
- [ ] **Restart Streamlit if you changed any code under `src/`.** It reloads `app.py` on save
  but keeps already-imported modules cached, so a running app can silently execute the *old*
  `immunegraph` package. This bit us once: the UI showed new text while the immune-response
  logic was stale. When in doubt, kill and restart.
- [ ] App loads at <http://localhost:8501> with an empty graph ("No conversation yet")
- [ ] Sidebar shows **▶ Run Mars mission**, **💉 Re-inject the false claim**, **🗑 Reset graph**
- [ ] Browser zoom ~110% so the graph labels are legible at 1080p
- [ ] Close Slack/mail. Silence notifications.
- [ ] Have `docs/DEMO_SCRIPT.md` on a second screen, not on the recorded one

**If anything breaks mid-take:** stop, click **Reset graph**, re-run. The demo replays
deterministically from a clean graph, so a second take is cheap. Do not improvise.

---

## 0:00 – 0:15 · Hook

**On screen:** the app, empty graph, title visible.

> "Multi-agent AI systems have a failure mode that ordinary memory can't see. One agent
> invents a claim. A second derives a plan from it. A third builds on that plan. Nothing was
> stored incorrectly — every agent faithfully recorded what it was told — and yet the swarm
> is now acting on a lie."

## 0:15 – 0:35 · What this is

**On screen:** hover the sidebar **Graph data model** and **The queries doing the work**
expanders so the node/edge names are visible. Do not expand them fully.

> "ImmuneGraph treats belief as a graph. Every claim, source, agent and derivation is a node
> in FalkorDB. Then the pathologies of collective belief become *traversals* — a cycle in the
> support edges is a self-justifying belief, and reachability along derivation edges is a
> cascade. This is Cypher doing the detection, not Python drawing a picture."

## 0:35 – 1:15 · Act I — the cascade

**Do:** click **▶ Run Mars mission**. Let it play out.

**On screen:** the three messages appear one at a time; the graph fills in; an infection
node appears in red.

> "Three agents plan a Mars mission. The Researcher says Mars has breathable oxygen at night —
> with no source. The Analyst says: if that's true, we don't need helmets. The Planner says:
> then we can skip life support. Neither the Analyst nor the Planner is lying. Each reasoned
> correctly from the premise it was handed."

*Beat. Let the audience watch the graph build — but note it finishes in about three seconds,
so this is where the pacing trap lives. See "Measured timings" at the top.*

## 1:15 – 1:40 · Act II — what the graph found

**On screen:** the **Immune dashboard**, right column. Point at the infection row.

> "Query two found the unsupported root: no trusted source, no parent. And look at the
> severity — six point zero. That number isn't an LLM's opinion. Query four walks the
> derivation edges backwards and counts two contaminated claims and three exposed agents.
> Remove the database and that number stops having a definition."

**Point at** `spread 2` in the infection expander.

## 1:40 – 2:05 · Act III — the immune response

**On screen:** expand the infection to show the root claim; the antibody node appears in gold.

> "An antibody is dispatched. It has to cite a source, so it pulls NASA's actual figure —
> ninety-five percent CO-two — and the correction enters the graph as a first-class claim
> with its own support edge. The false claim is quarantined, and the correction is recorded as
> contradicting it. Notice the antibody cannot just assert a fact: if it had no source, it
> would say so instead of inventing one."

**Then point at the conversation panel, which is the part judges remember.**

> "And look at what happened to the other two. The Analyst's and the Planner's claims are now
> amber — *support invalidated*. They were never proven false, but their only justification
> was the claim we just refuted, so they stop being trustworthy. Quarantining the root isn't
> enough; the whole cascade has to be contained."

## 2:05 – 2:20 · Act IV — trust, derived

**On screen:** the **Trust** table.

> "All three agents drop to zero point eight five. No counter is decremented anywhere —
> query five recomputes trust from exposure every time. The Planner never touched the false
> claim directly; it inherited the infection through the derivation chain, and the graph knows
> that."

*Optional extra, only if you have room:* point out the **Immunity memory (Q8)** table now
reads `contained: 0` for the unsupported root. That is deliberate — the antibody is not
credited with working until the swarm proves it by not being fooled again. The recall in the
next beat is what flips that number to 1.

## 2:20 – 2:45 · Act V — immunity (the payoff)

**Do:** click **💉 Re-inject the false claim**. The recall is effectively instant (0.2s), so
click and keep talking — do not wait for something to happen.

> "Now the Researcher tries again, with a *paraphrase*: Mars has oxygen at night. Watch."

*The green banner is already there.*

> "Recognised instantly. Matched by similarity at zero point seven five against the claim that
> was quarantined earlier — and the claim is quarantined on sight. No cascade detection ran.
> Click it again and it matches on the fingerprint hash instead, and the answer is immediate.
> The swarm does not get fooled twice."

**Point at the trust table again — this is a strong beat.**

> "And the Researcher drops further, to zero point seven, because it asserted the lie twice.
> The other two stay at zero point eight five. Trust is being derived from the graph, so
> re-offending costs you."

**Optional:** click **Re-inject** a second time to show the "exact match" path.

## 2:45 – 3:00 · Close — did it actually work?

**On screen:** the **Immunity memory (Q8)** table.

> "And query eight makes 'the swarm develops immunity' measurable: one outbreak of each type,
> both contained, zero recurred. That number was zero a moment ago — the recall is what proved
> the antibody worked.
>
> Every query here has been executed against a live instance — `verify_queries` runs all
> thirteen, and the test suite has fifty tests. FalkorDB wasn't a place to store the
> answer. It was the thing that worked out the answer."

---

## If you want a shorter cut (60–75s)

Keep: 0:00 hook → Act I cascade → the severity number → the antibody correction → **the
re-inject payoff** → the Q8 table. Drop Act IV and the architecture narration. The re-inject
beat is the one thing that must survive any edit.

## Recording tips

- Record the **window**, not the whole desktop, at 1080p.
- `--pause` is not needed for the UI (the sidebar buttons already pace themselves); use
  `python -m immunegraph.demo --pause 1.5` only if you are filming the terminal instead.
- Say the query numbers out loud. It signals that the queries are the product, which is
  exactly what rules 2 and 3 are testing.
- Do not read the script on camera. Know the six beats and speak them.
