# Per-track submission write-ups

Rule 5 requires naming **every** track entered. This project enters all three, so there are three
write-ups below. Each is self-contained, the short and long descriptions are written to be pasted
into a form field without editing.

**Shared links**

- Repository: https://github.com/TCPCore/immunity
- Demo video: *(host it and paste the link here, not committed to the repo)*
- Live deployment: *(Streamlit Cloud link, verify in an incognito window first)*
- Blog post: [`docs/BLOG_POST.md`](BLOG_POST.md) *(publish, then link)*

---

# Track 01, Agents That Act on Connected Data

## One-line pitch

Agents that read a belief graph, decide an outbreak is real, and act on it, dispatching a sourced
correction, quarantining the claim, and containing every downstream derivation.

## Short description (53 words)

ImmuneGraph gives a multi-agent swarm a graph of what supports what, so an unsupported claim is a
traversal rather than a hunch. When the swarm finds one, agents act: an infection is opened, an
antibody injects a sourced correction, the claim is quarantined, and every derivation from it is
invalidated. Anyone can submit a claim and watch it happen.

## Long description (196 words)

Agent memory records what was said. It does not record what supports what, so a swarm can be
perfectly internally consistent and still act on a lie: one agent invents a claim, others reason
correctly from it, and the conclusion is false.

ImmuneGraph models every claim, source, agent and derivation as a node in FalkorDB, which turns the
pathologies of collective belief into Cypher traversals, cycles in the support structure, active
claims with no source and no parent, contradiction clusters, and cascade reach. **Severity is not a
judgement call**: it is `1 + claims descending from the root + agents exposed to them`, computed by
traversing `DERIVED_FROM` edges. Remove the database and the concept ceases to exist, which is the
test of whether the graph is doing real work.

Detection is the easy half. The agents then act: an `Infection` targets the claim, an `Antibody`
neutralises it and injects a correction that cites its source, the original becomes `quarantined`,
and `contain_cascade` invalidates everything that reasoned from it.

Anyone can submit a claim the swarm has never seen. It is grounded against Wikipedia and pushed
through the same machinery, with the infection spreading through real agents before it is contained.

## What to look at

1. **Live demo** tab → **Run Mars mission**: watch severity reach 6.0 from a single unsourced claim.
2. **Check any claim** tab → type `Venus has a breathable atmosphere` → **Check this claim**. The
   cascade spreads through the Analyst and Planner (severity 2.0 → 4.0 → 6.0) and is then contained.

## Evidence

- 18 Cypher queries, all executed against a live instance before every commit (`verify_queries`).
- Severity 6.0 = 1 root + 2 derived claims + 3 exposed agents, computed by traversal, not stored.
- Trust derived from exposure, never written: the Planner drops to 0.85 without ever touching the
  false claim, because it inherited the infection through the derivation chain.
- Containment invalidates the whole cascade, not just the root.
- 162 tests, including the cascade ordering and containment.

- Self-audit: precision 1.00, recall 1.00, F1 1.00, autoimmunity 0%.

## Why it fits this track

The agents do not merely visualise a graph, they *decide and act* on it. The action is the removal
of a belief from circulation, and the justification for that action is itself a graph traversal.

---

# Track 02, Agent Memory and Coordination

## One-line pitch

A swarm memory that stores what was said, what is believed on what authority, and what to do when
it happens again, durable enough to rebuild itself from a log, proven identical.

## Short description (55 words)

ImmuneGraph keeps three kinds of memory, episodic conversation, semantic belief with provenance,
and procedural immunity that records which correction worked. Its kernel of truth means a repeated
lie is caught in 1.3 ms instead of a 20 ms graph search. Every write is logged first, so the graph
is a projection that can be rebuilt and verified identical.

## Long description (205 words)

Memory layers answer "what was said". A swarm also needs to know what it *believes*, on what
authority, and what to do the next time the same thing happens.

ImmuneGraph keeps all three. **Episodic** memory is `Message` and `Session`. **Semantic** memory is
`Claim`, `Source` and `KernelFact`, with provenance as a traversal, so "which source justified
this" is a query rather than a citation a model wrote from memory. **Procedural** memory is
`Antibody` and `Infection`: not what happened, but what to do when it happens again, including the
recorded outcome, so effectiveness is measured rather than assumed.

The distinction that matters is two memories of opposite polarity. `Infection` records what was
**wrong**; `KernelFact` records what is now held **true**. That is what makes immunity cheap, a
repeat is recognised against a handful of facts in **1.3 ms median** instead of a **20.3 ms** graph
sweep, and every correction moves work from the expensive path to the cheap one.

Coordination is derived, not stored: trust is recomputed from each agent's exposure, so it cannot
drift out of sync with the belief graph. And because FalkorDB Cloud's free tier has no persistence,
every write is appended to a log before it is applied, 73 writes, 0 failures, rebuilt identical
including node ids.

## What to look at

1. **Kernel & durability** tab → the facts in force, with the antibody and source behind each.
2. **Rebuild from log** → wipes the graph and reconstructs it, then reports whether it is identical.
3. **Live demo** → **Re-inject the false claim**: caught in milliseconds, no cascade rescan.
4. **Immune dashboard** → trust and Q8 effectiveness, both derived.

## Evidence

- Kernel fast path **1.31 ms median / 2.25 ms p95** (local); detection sweep **20.3 ms**, a ~15x
  difference, and the reason the two are used differently.
- Rebuild: **73 writes, 0 failed, identical**, including node ids, which is why the log stores
  statements rather than semantic events.
- Concurrency: naive `max(seq)+1` produced **37 distinct sequence numbers** across 200 concurrent
  writes; allocating with a `SET` gives **200/200**.
- Quarantine precedence: a stronger verdict is never clobbered by a weaker one.

## Why it fits this track

The submission is about memory that a swarm can actually rely on: three kinds of memory, a
correction that becomes retrievable truth, coordination derived from the graph, and durability
proven rather than asserted. The concurrency fix is the part most likely to matter in production.

---

# Track 03, Company Brain

## One-line pitch

A shared company brain over its own documents, where a wrong price cannot spread between
departments unnoticed, and a policy change automatically retires the old truth.

## Short description (54 words)

ImmuneGraph loads a company's documents into a graph of facts with provenance, so every department
agent answers from the same ground truth. When a sales agent invents a price and support repeats it,
the conflict is caught against the pricing document in 7 ms. When the price changes, the old fact is
superseded.

## Long description (203 words)

The characteristic failure of a company brain is not retrieval failure. It is that one agent's
confident wrong **price** gets repeated by another department until it becomes doctrine, and a wrong
price is a million-dollar hallucination, not an embarrassing paragraph.

ImmuneGraph loads the company's documents as `Source` nodes and the facts they establish as
`KernelFact`s linked to them, so provenance is a traversal rather than a citation a model wrote from
memory. Department agents answer from that shared brain. When Sales invents a price, Support repeats
it with a real `DERIVED_FROM` edge back to Sales, a cascade, not two coincidences, and the
infection is contained by an antibody that cites the actual pricing document.

Two things a company brain needs beyond a generic agent memory. **Quantity conflicts:** everything a
company states as fact is a number, and "399 per seat" does not negate "499 per seat", the two agree
on every word except the one that matters. Negation-based detection scored the wrong price as
*aligned* with policy; a dedicated numeric check now catches it. **Policy drift:** when the price
changes, the old fact is superseded rather than deleted and every active claim is re-judged, so the
answer that was correct last quarter is flagged instead of quietly remaining blessed.

The corpus is synthetic, so no private information is involved.

## What to look at

1. **Company brain** tab → **Ask the company brain**: four documents load as sources, then the wrong
   price spreads and is contained.
2. Watch the **revision** step: the old fact is superseded, and the previously-correct price is
   immediately flagged.
3. **Facts in force**, each fact shown with its trust level and the document it came from.

## Evidence

- 4 documents → 5 kernel facts, each linked to its source document.
- Wrong price caught in **7.2 ms** local / 97 ms cloud against the pricing policy.
- Numeric conflict detection verified to fire on `399 vs 499` and **not** fire between a refund
  window and an incident SLA, which share digits but no subject.
- After the revision, the previously-correct 499 claim is `suspect`, nothing stale stays blessed.
- Superseded facts are retained, so history stays answerable.

## Why it fits this track

It is a company brain that answers from written ground truth, shows its work through provenance, and
handles the two things that actually break enterprise agent deployments: a wrong number spreading
between teams, and last quarter's answer still being served.

---

# Submission checklist (rule 10)

- [x] Public source-code repository, https://github.com/TCPCore/immunity
- [x] README with setup instructions and the graph data model
- [x] The Cypher queries the product depends on, `src/immunegraph/queries.py`, all executed by
      `python -m immunegraph.verify_queries`
- [ ] Demo video, recorded, **needs hosting**; paste the link above
- [ ] Live deployment, Streamlit Cloud app exists but currently **requires login**; set
      *Settings → Sharing → anyone with the link* and verify in an incognito window. If it cannot be
      made public, the README's Docker instructions satisfy the "complete local setup" alternative.
- [x] The tracks entered, all three, named above
- [x] AI assistant use disclosed (see README)
