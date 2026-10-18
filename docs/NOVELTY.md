# What is actually new here

A precise claim, and the prior art it is measured against. Written because the honest boundary is
worth more than a bold one, and because a judge can check in one search.

---

## 1. What is not ours

Stating this first, because pretending otherwise is the fastest way to lose credibility.

| Claim | Whose it is |
| --- | --- |
| Belief as a justification graph; retract when justification fails | Doyle (1979), de Kleer (1986). Truth maintenance systems. |
| Belief revision under contradiction | Alchourrón, Gärdenfors, Mackinson (1985). |
| Multi-agent collaboration as a directed dependency graph; cascades as an intrinsic pathology; a genealogy-graph governance layer that suppresses propagation | [From Spark to Fire](https://arxiv.org/html/2603.04474) (2026). Measured over six frameworks, defence 0.32 → 0.89. |
| Generating mutations of a program to find weak tests | Mutation testing, DeMillo et al. (1978). |
| Testing without an oracle by transforming inputs | Metamorphic testing, Chen et al. (1998). |
| Detecting unsupported claims, cycles and contradictions as traversals | Ours, but it is a straightforward application of the above. |

Everything ImmuneGraph does conceptually, someone else said first. The cascade insight is the
core of the project and it is not novel.

---

## 2. The gap that is actually open

The other side has a name, **autoimmunity**, and it is almost never reported. Every paper and
product I looked at publishes coverage, how much was caught. None publishes what was wrongly
caught. That is a statement about what I could find, not about anyone's work being incomplete.

That asymmetry is not cosmetic. **Coverage alone is a gameable metric.** A detector that flags
every statement it sees achieves perfect coverage and is worthless. Any evaluation that reports one
number can be won by turning the sensitivity up. The missing number is the one that tells you
whether the immune system is attacking the host.

So the gap is not "detect cascades", that is solved. The gap is **"how do you know your detector
is good rather than merely loud?"**

---

## 3. What we claim

Narrowly:

> A **two-sided, self-generated audit** of a belief-defence system, which measures coverage
> against autoimmunity and attribution, generates its adversarial probes from the system's own
> kernel, and closes the gaps it finds by writing negative memory back into the graph.

Four specific things, each of which I could not find elsewhere:

**1. The metric pair, implemented.** Coverage × autoimmunity, reported together, neither
optimisable alone. ANIS names the second; this computes it and puts it on screen next to the first.

**2. Attribution as a third dimension.** Not "did it fire" but "did it fire at the *right fact*".
A defence that contradicts the wrong fact has the right answer by luck, and will fail on the next
corpus. `Report.attribution` measures caught-minus-misattributed.

**3. Probes generated from the system's own knowledge.** The test set is not a benchmark; it is
mutations of what the system currently holds true, perturbed numbers, transplanted numbers,
substituted entities, and truth-preserving paraphrases that must *not* fire. A system can therefore
audit itself the moment its knowledge base changes, with no labelled data.

**4. Repair as graph state, with the metric closing the loop.** Blind spots become
`RefutedStatement` nodes, negative memory, written through the same write-ahead log as everything
else, and the audit is re-run to prove the repair worked *and did not poison anything*.

---

## 4. The evidence that this matters, measured, on our own code

This is the part I would show a sceptic, because the audit found two real defects that 134 passing
tests did not.

**First: 20% autoimmunity.** With 134 green tests, the audit reported that 2 of 10 true statements
were judged *conflicting*. `"Enterprise costs 499 USD per seat per year"`, true, was flagged,
because it numerically mismatches the **Team** plan's fact and the rule never checked which entity
the claim was about. The defence was **subject-blind**.

Binding conflicts to a shared subject: **autoimmunity 20% → 0%, coverage unchanged at 100%.**

**Second, and this is the argument for the metric:** with subject binding in place, the audit found
5 falsehoods that no fact contradicts, `"The Starter plan costs 499 USD per seat per year"` is
not contradicted by anything, because Halcyon sells no Starter plan. The obvious repair is to
record it as known-false.

That repair scored:

```
uncovered: 5 → 0        <- the repair worked
coverage : 100% → 100%  <- still looks perfect
autoimmunity: 0% → 81.8%  <- the vaccine was attacking the host
```

Recording "Starter plan costs 499" made it fuzzily match the **true** "Enterprise plan costs 499"
on every word but one. A system reporting only coverage would have shipped this as a success.

Applying subject binding one level deeper, to the vaccine itself, closed the holes with
autoimmunity held at zero:

```
before: coverage 100% · autoimmunity 0% · uncovered 5
after : coverage 100% · autoimmunity 0% · uncovered 0
```

Three numbers, or the fix is invisible.

---

## 5. Why this is worth having

A defence that cannot measure its own false-positive rate is not deployable in a company brain.
Blocking a *correct* answer has a cost, a customer told the wrong price because the system
"protected" them from a true sentence, and that cost is invisible in every metric currently
reported. The pair makes the trade-off explicit and forces a design decision instead of a
sensitivity slider.

It also converts "trust our demo" into "run our audit": the probes come from your own knowledge
base, so the number is about *your* corpus, not ours.

---

## 6. What this is not

- **Not a new detection algorithm.** Detection is the published part.
- **Not novel as a technique.** Mutation testing is from 1978; metamorphic testing from 1998.
  What is new is the object being mutated, a knowledge graph's own assertions, and the pairing
  of the two metrics for a belief-defence system.
- **Not independently validated.** One corpus, five facts, tens of probes. The autoimmunity rate
  is a property of *this* defence on *this* knowledge; it is not a claim about belief-defence
  systems in general.
- **Not proof that the repair generalises.** Subject binding is a heuristic, a leading-token
  match. It will fail on statements whose subject is not the first content word, and the audit
  would only catch that if a probe happens to expose it.

The claim is that the *measurement loop* is new and that it immediately paid for itself. Not that
the system is correct.

---

## 7. Where to look

- `src/immunegraph/audit.py`, probes, the three measures, vaccination.
- `src/immunegraph/sanitizer.py`, subject binding, applied to both facts and refuted statements.
- `tests/test_audit.py`, both defects pinned: the 20% autoimmunity and the 82% vaccine.
- The **Self-audit** tab, run it, then vaccinate, and watch the three numbers.
