# Demo video script

Target: **2:40**. Rule 10 requires a demo video showing the working project, so this is
scripted beat by beat.

The goal is that a judge who watches only the video understands that **the graph is the
immune system**, not a visualisation bolted onto storage.

---

## Pre-flight (do this before you hit record)

```bash
docker compose up -d
.venv/bin/python -m immunegraph.verify_queries    # must end with "All non-optional queries..."
.venv/bin/streamlit run app.py
```

- [ ] FalkorDB healthy: `docker ps` shows `immunegraph-db  Up ... (healthy)`
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

*Beat. Let the audience watch the graph build.*

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

## 2:05 – 2:20 · Act IV — trust, derived

**On screen:** the **Trust** table.

> "All three agents drop to zero point eight five. No counter is decremented anywhere —
> query five recomputes trust from exposure every time. The Planner never touched the false
> claim directly; it inherited the infection through the derivation chain, and the graph knows
> that."

## 2:20 – 2:45 · Act V — immunity (the payoff)

**Do:** click **💉 Re-inject the false claim**. Pause on the green success banner.

> "Now the Researcher tries again, with a *paraphrase*: Mars has oxygen at night. Watch."

*Beat — the success banner appears.*

> "Recognised instantly. Matched by similarity at zero point seven five against the claim that
> was quarantined earlier — and the claim is quarantined on sight. No cascade detection ran.
> Click it again and it matches on the fingerprint hash instead, and the answer is immediate.
> The swarm does not get fooled twice."

**Optional, if you have time:** click **Re-inject** a second time to show the "exact match"
path and prove it does not crash.

## 2:45 – 2:40 · Close — did it actually work?

**On screen:** the **Immunity memory (Q8)** table.

> "And query eight makes 'the swarm develops immunity' measurable: one outbreak of each type,
> both contained, zero recurred.
>
> Every query here has been executed against a live instance — `verify_queries` runs all
> thirteen, and the test suite has twenty-eight tests. FalkorDB wasn't a place to store the
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
