# Applicability — what this is actually good for

Written against measured results, not intentions. Every number below was produced by running the
code; where something is unproven it says so.

---

## 1. What is measured

| Claim | Measured | How |
| --- | --- | --- |
| Every Cypher query executes and returns the expected shape | **18/18** | `python -m immunegraph.verify_queries` against live FalkorDB, local **and** cloud |
| Kernel fast path (judge one claim against the kernel) | **1.31 ms** median, **2.25 ms** p95 | local Docker, 30 judgements |
| Kernel fast path over a network | **21 ms** median | same code against FalkorDB Cloud |
| Full detection sweep (Q1+Q2+Q3) | **20.3 ms** | local Docker, 12-node graph |
| Full detection sweep over a network | **234 ms** | FalkorDB Cloud, same graph |
| A real policy conflict (wrong price vs pricing doc) | **7.2 ms** local / **97 ms** cloud | company scenario |
| Rebuild from the write-ahead log | **73 writes, 0 failed, identical** | including node ids |
| Concurrent sequence allocation | **200/200 unique** under 8 writers | naive `max(seq)+1` gave 37 |
| Grounding verdicts | correct on the test cases, **inconclusive on nonsense** | `tests/test_grounding.py` |
| Regression suite | **134 tests** | `python -m pytest` |

**Two things to read carefully.**

The gap between local and cloud is **pure network round-trips**. The same query is 16x slower over
the network. So the compute is cheap and the *topology* is what you optimise — deploy the graph
near the agents, and the fast path is genuinely sub-millisecond.

The graph here is **12 nodes**. Nothing in the table says how detection behaves at ten thousand
claims. See §5.

---

## 2. The problem it solves, stated precisely

> An agent asserts something unsupported. Other agents derive from it — correctly. The swarm now
> believes something false, and **every individual step was valid.**

This is not a hallucination-detection problem. A single LLM hallucinating is well understood and
mostly caught by grounding or citations. The failure here is *structural*: the swarm's belief
graph contains an unsupported root, and everything downstream inherits its falsehood while looking
perfectly reasoned.

The thing that makes it expensive is that **the errors are correlated**. If three agents
independently hallucinate, you get three bad outputs. If one hallucinates and two derive from it,
you get one bad output that three agents agree on — and agreement is what humans use as a signal of
truth.

Three mechanisms address it, and they are separable:

1. **Detect** — find unsupported roots, cycles and contradictions as graph traversals.
2. **Contain** — quarantine the root, invalidate the derivations, record what went wrong.
3. **Remember** — keep the corrected fact in a kernel so the same lie is caught in milliseconds
   instead of seconds.

---

## 3. Where it applies

The pattern is worth money wherever **agent output is acted on and the cost of a wrong answer
exceeds the cost of checking it.** Ordered by how directly the existing demo supports them.

### 3.1 Customer-facing agent fleets — support, sales, quoting

*Failure mode:* an agent states a wrong price, refund window or SLA. Another agent repeats it to a
customer. It is now a commitment the company may have to honour.

*What this gives you:* policy documents become the kernel; every outbound answer is checked in
**1–2 ms** against them; a conflict blocks or escalates before a human sees it. This is the
company-brain scenario in this repo, running end to end.

*Why it is the best first target:* the facts are few, stable and written down. Prices, SLAs and
policy dates are exactly the kind of thing a kernel holds well, and the cost of a wrong answer is
immediate and measurable.

### 3.2 Multi-agent analysis pipelines — research, due diligence, risk

*Failure mode:* a research agent misreads a filing; an analyst agent builds a model on it; a
writing agent produces a confident report. Nobody is lying and nothing looks wrong.

*What this gives you:* after a pipeline run, a sweep finds the unsupported root and reports
**severity** — which is defined as reach through the derivation graph, not as a confidence score.
Severity becomes a **triage queue**: review the top three cascades, not all ninety outputs.

*The value here is prioritisation, not automation.* You are not replacing the human review; you are
telling the reviewer where to look first.

### 3.3 Regulated domains — finance, insurance, healthcare documentation

*Failure mode:* a decision was made by agents and, six months later, someone asks **why**.

*What this gives you:* provenance is a traversal. "Which source justified this claim, which
antibody established this fact, what did we believe on the day this decision was made" are all
queries, not reconstruction. `supersede` keeps retired facts rather than deleting them, so the
answer is historically accurate.

*This is the capability with the least glamour and the most durable commercial value.* Traceability
is a compliance requirement in more jurisdictions every year, and most agent stacks cannot answer
"why" at all.

### 3.4 RAG output verification

*Failure mode:* retrieval was correct, and the answer drifted from the retrieved document anyway.

*What this gives you:* the document is already what you retrieved; check the *answer* against it
before it ships. This catches the dominant practical RAG failure — the answer that cites a source
and contradicts it — which retrieval quality metrics never see.

### 3.5 Coding and engineering agents

*Failure mode:* an agent assumes an API behaves a certain way; a plan is built on the assumption;
the plan is executed.

*Claims are testable:* "this function returns a list", "this endpoint requires auth". Cycles in the
support graph catch self-justifying plans. Unsupported roots catch assumptions nobody verified.

*Honest caveat:* this is the domain where developers have the most alternatives, and where a wrong
assumption is usually caught by a test. The value is real but less differentiated here.

---

## 3b. What the audit is worth

A medical test that never misses but always cries wolf is useless — and until now nobody in this
field published the false-positive rate.

The audit's value is **meta**: it does not make detection better, it makes detection *deployable*.
Every claim in this space is about what a guardrail catches. Nobody prices what it *wrongly*
catches, and a false positive in an agent system does concrete damage:

| False positive | Real consequence |
| --- | --- |
| Correct price flagged as conflicting | A customer is told the wrong price by a system "protecting" them from a true sentence |
| Correct claim quarantined | Knowledge is silently lost; the swarm gets less capable, not safer |
| Agent wrongly penalised | Trust scores corrupt, and the graph now holds a false accusation |
| Correct answer blocked inline | The product feels broken to the user |
| Alerts reviewed by humans | Alert fatigue — people stop trusting it and switch it off |

That last row is the reason this matters commercially: **an untrustworthy guardrail gets disabled**,
and then it protects nothing. The false-positive rate is not a quality metric, it is a survival
metric.

We measured our own: **20% autoimmunity with 134 tests passing.** The fix took it to zero. The
obvious repair for the next gap drove it to **82.8% while coverage still read 100%**.

### What the number unlocks

Four decisions that cannot be made without it:

1. **Inline or async.** Our kernel check is 1.31 ms locally, 21 ms over a network. At low
   autoimmunity you can put it in the request path and block. At high autoimmunity you must not,
   because you would block correct answers. The audit tells you which system you have.
2. **Ship or hold.** A pre-deployment gate: autoimmunity above a threshold (say 2%) fails the
   build. A CI step, not a judgement call.
3. **Which guardrail.** Run the same audit against two vendors on *your* corpus. Today "99%
   detection" is unfalsifiable; with a pair it is comparable.
4. **Did the fix help?** Knowledge bases change constantly. Re-run the audit; autoimmunity drift
   tells you a policy edit broke the checker.

### Verticals, ranked by the cost of a false positive

**Tier 1 — high cost, written-down ground truth (best fit):**

- **Customer support and sales agent fleets.** A blocked valid quote is lost revenue, and the
  corpus is policies and price lists, which the audit handles well. This is the demo, running.
- **Financial services compliance.** A false positive blocks a transaction or fires a report; heavy
  audit requirements map onto provenance.
- **Legal and contract review.** Cost per false positive is a lawyer's hour, and the corpus is
  documents — what a kernel is good at.

**Tier 2 — high stakes, harder corpus:**

- **Healthcare documentation.** Flagging a correct clinical statement causes alert fatigue and can
  suppress valid notes. *Caveat: clinical facts are less "written down" than prices, so probe
  quality would be lower.*
- **Insurance claims triage.** Flagging valid claims delays payouts and creates regulatory exposure.

**Tier 3 — lower cost, high volume:**

- **Engineering agents.** Blocking a correct suggestion is annoying rather than expensive, but the
  audit is nearly free, so the ratio still works.
- **Any RAG system with citations.** The "answer contradicts its own source" checker is this exact
  problem.

**Horizontal — platform vendors.** Whoever sells agent memory or guardrails can publish an
autoimmunity rate as a differentiator. Largest value if it lands, because it turns a feature into a
standard.

### What the value is not

- **It does not make detection better.** It makes it measurable. Value accrues only if someone acts
  on the number.
- **Coverage here is synthetic.** The probes are mutations of your own facts — a proxy for realistic
  errors, **not** the real distribution of false claims in your traffic. High synthetic coverage does
  not prove production coverage.
- **One corpus, five facts, ~24 probes.** The 20% → 0% → 82% → 0% sequence demonstrates that the
  *loop* works; it is not a validated benchmark.
- **The heuristic is crude.** Subject binding is a leading-token match and will fail on sentences
  whose subject is not the first content word.

The honest commercial statement: **this is the instrument, not the cure.** Its value is turning
"we believe our guardrail is safe" into a number that can go in a deployment review — and on our own
code it found a failure, then caught the fix making it three times worse.

---

## 4. How to apply it — four integration patterns

You do not rewrite your agents. You add a **write path** and choose where to check.

### Pattern A — inline guardrail (highest value, lowest risk)

```
agent produces an answer
        │
        ├─ record_claim(...)              # one write
        ├─ sanitizer.judge(...)           # 1–2 ms local
        │
        ├─ clean    ──► send to customer
        └─ conflicts ─► block / escalate / append correction
```

Put it on the **outbound** boundary, not inside the agent. You get protection without changing
prompt engineering, and you can enable it for one agent at a time.

### Pattern B — asynchronous cascade detector

Run the sweep on a schedule or at the end of a pipeline run. Do not put it in the request path:
it is 15x more expensive than the fast path and grows with the graph.

Use it to produce a **severity-ranked review queue**, not to block anything.

### Pattern C — trust and audit layer

Write every claim with its `ASSERTS` / `SUPPORTED_BY` / `DERIVED_FROM` edges as they happen, and
do the analysis later. Reading the graph answers "why", "on what authority", and "what changed".

This is the cheapest to adopt because it never blocks a request.

### Pattern D — document ingestion

Seed a kernel from a document store — policies, specs, contracts, a handbook. Every fact carries a
`SUPPORTED_BY` edge to the document and the quote. Then Pattern A becomes possible for that corpus.

**Mechanics that matter in practice**

- **Wrap, don't intrude.** Every agent framework has a per-message hook (a LangGraph node, a CrewAI
  task callback, an AutoGen event). Emit a claim there.
- **Derivation edges come free** if your orchestrator already knows parent/child turns — that is
  the edge that makes cascades visible. Without it you only have a list of claims.
- **Deploy the graph near the agents.** The 16x local-vs-cloud gap is network, not compute.
- **Start with facts you would bet money on.** Prices, SLAs, dates, limits. These are where a
  lexical match is reliable and where a wrong answer actually costs something.

---

## 5. What is not proven

This matters more than the list above, because the honest boundary is what makes the rest usable.

- **No labelled evaluation set.** There is no precision/recall figure for conflict detection. The
  tests assert behaviour on hand-written cases, not accuracy on a corpus. Anyone deploying this
  should build that set first.
- **Matching is lexical.** A contradiction expressed in different vocabulary is missed. The
  grounding layer returns *inconclusive* rather than guessing, which is safer but still a ceiling.
- **Scale is untested.** Every measurement here is on a graph of tens of nodes. Query cost at
  10,000 claims is unknown, and the vector index has never been exercised at that size.
- **Corrections were not evaluated for correctness.** In the Mars scenario the antibody is
  rule-based; an LLM-backed antibody can be wrong, which is why it must cite a source and why its
  outcome is recorded.
- **The free tier has no persistence.** The write-ahead log protects against a graph wipe during a
  session; true cross-restart durability needs durable storage.
- **No user study.** Whether severity-ranked triage actually reduces review time has not been
  measured with humans.
- **Multi-tenancy is in the schema, not exercised.** `tenant_id` exists; no isolation test does.

---

## 6. What to build next, in order

1. **A labelled corpus.** 200–500 real claims, marked conflicting / aligned / unrelated, with
   precision and recall published. Nothing else matters until this exists — without it the system
   is demo-grade.
2. **Semantic conflict detection.** Embeddings, then an LLM judge for the ambiguous band, with the
   graph still recording *why* the decision was made. Keep the deterministic path as the fast lane.
3. **Ingestion connectors.** Confluence, Notion, Slack, GitHub, a filesystem watcher. A kernel is
   only as good as the corpus feeding it.
4. **A scale benchmark.** 10k and 100k claims, with query latency and index behaviour recorded.
   This determines whether Pattern A stays inline.
5. **Enforcement.** Move from *flagging* to *blocking* outbound answers, with an audited override.
6. **Framework integrations** as reusable packages rather than a reference implementation.

---

## 7. The commercial argument in one paragraph

A wrong price in a quote, a wrong clause in a contract summary, a wrong figure in a due-diligence
report — each is discoverable, expensive, and currently caught by luck or by a human reading
everything. The check costs **one to two milliseconds** against a local graph. So the trade is:
pay a rounding error of latency on every answer, and in exchange get a blocking check against
written policy, a severity-ranked triage queue for the rest, and a provenance graph that answers
"why did we believe this" six months later. The memory is the part that compounds: every correction
makes the *next* occurrence cheaper to catch, which is exactly how an immune system pays for itself.

**Where it is not worth it:** low-stakes, high-volume, no written ground truth. If nobody would
lose money from a wrong answer, and there is no document to check against, the kernel has nothing
to hold.
