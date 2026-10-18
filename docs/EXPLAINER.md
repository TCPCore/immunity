# Explainer, how to defend this project

Written for **rule 12** ("participants must understand the submitted code and be able to
explain the graph model, the queries, the project architecture, and the technical
decisions"). Reading this is not the same as understanding it, but this is the material
you need to be able to reproduce unaided.

---

## 1. The sixty-second version

Multi-agent systems fail in a way ordinary memory layers cannot see. One agent invents a
claim. A second derives a plan from it. A third derives a plan from *that*. Nothing is
stored incorrectly, every agent faithfully recorded what it was told, and yet the swarm
is now acting on a fabrication. Existing memory answers *"what was said?"*; none of them
answer *"is this swarm getting epistemically sick?"*

ImmuneGraph models belief as a graph, so the pathologies become traversals. A cycle in the
support edges is a self-justifying belief. A claim with no source and no parent is an
unsupported root. Reachability along derivation edges is a cascade. When a query finds one,
we create an `Infection` whose **severity is computed from graph reachability**, dispatch an
antibody that injects a sourced correction, quarantine the claim, and record the episode so
the *next* occurrence is caught instantly. The swarm develops immunity.

---

## 2. Why a graph at all, and why FalkorDB is load-bearing

The honest test a judge will apply is rule 3: *if FalkorDB were removed, would the product
still work?* Here is the answer, and you should be able to give it cold.

**Severity is defined as graph reach.** Query Q4 walks `[:DERIVED_FROM*1..5]` backwards from
a root claim to count how many downstream claims and exposed agents it contaminated, and
`severity = 1 + infected_claims + exposed_agents`. That number is not stored analysis, it
*is* a traversal. Delete the graph and "how bad is this outbreak" stops having a definition.

**Trust is derived, not stored.** Q5 recomputes every agent's trust from their association
with unresolved infections. Nothing decrements a counter, so trust can never drift out of
sync with the belief graph. In a vector store you would be maintaining a denormalised score
that silently goes stale.

**Recall is a graph lookup.** Immunity recall (Q6) is an indexed equality match on
`Claim.fingerprint` restricted to claims that carry an `Infection`. The immune memory is not
a side-table; it is the `Infection`→`TARGETS`→`Claim` subgraph.

**The cascade is a path, not a score.** Q1 finds *cycles*, a property of graph topology
that has no meaningful analogue in a vector store or a relational table without recursive
CTEs.

A useful contrast to have ready: **GraphRAG-style memory stores what was said; ImmuneGraph
stores what supports what.** Adding a vector index (we do, for Q7) makes retrieval better but
does nothing for any of the four properties above.

---

## 3. The schema in three families

The track-02 rubric asks for episodic, semantic and procedural memory. We model them as
separate node families rather than one generic blob:

| Family | Nodes | Property |
| --- | --- | --- |
| **Episodic** | `Message`, `Session` | Append-only, timestamped, ordered by `seq`. Never mutated. |
| **Semantic** | `Claim`, `Source`, `Agent` | Durable beliefs and who holds them. `Claim.status` changes over time. |
| **Procedural** | `Antibody`, `Infection` | *How the swarm responds* to a class of failure, reused on later outbreaks. |

The bridge is `(:Agent)-[:ASSERTS]->(:Claim)` with the originating `Message` linked via
`(:Message)-[:MENTIONS]->(:Claim)`. So you can always answer both "what did the agent say?"
and "what does the swarm believe?", and trace one to the other.

`DERIVED_FROM` runs **child → parent**. This is the single most common thing to get
backwards, and it matters: the cascade walks `(downstream)-[:DERIVED_FROM*1..5]->(root)`,
pointing *at* the falsehood, not away from it. Be ready to draw the arrow on a whiteboard.

---

## 4. The queries, and the traps in them

All eight live in `src/immunegraph/queries.py` and are proved by
`python -m immunegraph.verify_queries`.

| Query | Finds | The trap |
| --- | --- | --- |
| **Q1** | cycles in `SUPPORTED_BY`/`DERIVED_FROM` | alternation inside a variable-length pattern works on this build, but a 3-cycle is reported from **all three** starting nodes, dedupe before counting outbreaks |
| **Q2** | active claims with no trusted source and no parent | written with `OPTIONAL MATCH` + `count()`, **not** a negated pattern predicate, to stay inside FalkorDB's openCypher subset |
| **Q3** | contradictions between two asserting agents | the `CONTRADICTS` edge is written at extraction time; this query only surfaces it |
| **Q4** | cascade reach and exposed agents | this is where severity comes from. `collect(DISTINCT down)` then `downs + [root]` avoids `UNWIND` on an empty list |
| **Q5** | trust derived from two kinds of exposure | see below |
| **Q6** | exact repeat of an infected claim | indexed on `fingerprint`; O(1)-ish, which is why recall feels instant |
| **Q7** | paraphrase of an infected claim | see below, two non-obvious behaviours |
| **Q8** | antibody effectiveness per infection type | turns "the swarm develops immunity" into a measurement |

### Q5, why it counts *two* kinds of exposure

The obvious version counts only agents who directly asserted an infected claim. That is
wrong for this product: an agent that *derives* a plan from someone else's fabrication has
propagated it. So Q5 counts both direct assertion and assertion of a claim reachable from an
infected root:

```cypher
OPTIONAL MATCH (a)-[:ASSERTS]->(c:Claim)<-[:TARGETS]-(i1:Infection) ...
OPTIONAL MATCH (a)-[:ASSERTS]->(d:Claim)-[:DERIVED_FROM*1..5]->(:Claim)<-[:TARGETS]-(i2:Infection) ...
```

In the Mars demo this is what makes Researcher, Analyst **and** Planner all lose trust,
which is the emotional payoff of the cascade.

### Q7, two things you only learn by running it

1. The query vector **must** be wrapped in `vecf32(...)`. Passing a bare list, or a stored
   vector property directly, raises `Invalid arguments for procedure
   'db.idx.vector.queryNodes'`.
2. The yielded `score` is a cosine **distance** (0.0 = identical) even though the index
   option is named `similarityFunction`. So `ORDER BY score DESC` returns the *least*
   similar candidates. With fewer claims than `k` you would never notice, it is a silent
   correctness bug that only appears at scale. We order ascending.

That second one is worth telling judges about, because the verifier did *not* catch it. It
was caught by reading the returned numbers and noticing the exact match scored highest while
being sorted last. This is the concrete example to use when asked how you verified the work.

---

## 5. Five decisions, and what they cost

1. **`Claim.fingerprint` instead of LLM-emitted `REPEATS` edges.** Normalised token hash in
   deterministic Python. *Cost:* exact-match recall misses paraphrases, so Q7 exists as the
   semantic path. *Benefit:* the demo's most important beat cannot fail on a model's whim.
2. **Severity from graph reach, not LLM judgement.** *Cost:* a cruder severity than a model
   might produce. *Benefit:* it is explainable and reproducible, and it is the strongest
   possible answer to rule 3.
3. **Derived trust rather than a mutated counter.** *Cost:* recomputed on read. *Benefit:*
   cannot desynchronise.
4. **Dual extractor (LLM + deterministic rules).** *Cost:* two code paths to maintain.
   *Benefit:* the graph queries are real in both modes; only natural-language parsing
   changes, so a flaky API cannot kill a live demo.
5. **One graph per tenant via `select_graph`.** *Cost:* cross-tenant analytics need fan-out.
   *Benefit:* physical isolation, which is what track 02 asks for.

---

## 6. Questions you should expect

**"Isn't this just GraphRAG with extra steps?"**
GraphRAG retrieves context to improve an answer. This detects a *property of the belief
graph*, that it has become self-justifying or unsupported, and then acts on it. Retrieval
quality is orthogonal; you can have perfect retrieval of a fabrication.

**"How is this different from Zep/Graphiti or Mem0?"**
Those are memory layers: they persist and retrieve what agents said and did. They have no
notion of support structure, contradiction, cascade reach, or an immune response. Our
`SUPPORTED_BY`/`DERIVED_FROM`/`CONTRADICTS` topology is the product.

**"What stops the antibody from being wrong too?"**
Nothing, and that is a real limitation. The antibody is an LLM; we constrain it to cite a
`Source`, we store the correction as a first-class `Claim` with its own support edges, and
`Antibody.outcome` (Q8) records whether the correction actually held. A wrong antibody is
therefore visible in the graph rather than silently trusted. The honest answer is that this
is a *containment* system, not a truth oracle.

**"Why is a cycle bad? Isn't mutual support fine?"**
Mutual support between two *independently sourced* claims is fine. A cycle where the only
thing supporting each claim is the next one in the loop means there is no external grounding
anywhere in the loop, the belief is self-justifying by construction.

**"Could an attacker game it?"**
Yes. An agent could assert many well-sourced false claims and the system would trust them.
Detecting *sourced* falsehoods requires source-level reputation, which we model as
`Source.trust_level` but do not reason about deeply. Worth saying plainly rather than
overclaiming.

---

## 7. Limitations to state before a judge finds them

- Severity's formula (`1 + claims + agents`) is a deliberate simplification, not a tuned
  metric.
- The LLM extractor can mis-segment claims; the rule extractor is narrower but cannot fail.
- Contradiction detection is lexical (negation asymmetry plus token overlap), so it will
  miss contradictions phrased with entirely different vocabulary.
- Thresholds (`similarity` 0.25, trust decay 0.15) are chosen for demo legibility, not fitted
  to data.
- `falkordblite` cannot be used on x86_64 macOS because its published wheels ship arm64-only
  binaries, that is why this project requires Docker or FalkorDB Cloud.
