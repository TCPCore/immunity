"""The load-bearing Cypher.

These queries ARE the product: detection, severity, trust and recall are all
graph traversals. Every statement here mirrors docs/GRAPH_MODEL.md section 5
and must be exercised by verify_queries against a live instance.

FalkorDB implements a subset of openCypher v9. Constraints honoured here:
  * no regex operator
  * no label expressions
  * no aggregation inside pattern comprehensions
  * LIMIT does not constrain eager writes
  * unnamed relationships only verify existence
  * indexes are not used for `<>` filters
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuerySpec:
    """A named query plus what the caller must supply."""

    name: str
    cypher: str
    description: str
    required_params: tuple[str, ...] = ()
    optional: bool = False
    """Optional queries target features that may not exist on a given build
    (for example the vector index). They are reported, not failed."""


# --------------------------------------------------------------------- Q1
# A supports B supports C supports A: the swarm's belief is self-justifying.
Q1_CIRCULAR_SUPPORT = """
MATCH path = (c:Claim)-[:SUPPORTED_BY|DERIVED_FROM*2..5]->(c)
RETURN c.id AS claim_id,
       c.text AS claim,
       [n IN nodes(path) | n.text] AS cycle_path,
       length(path) AS cycle_length
"""

# Fallback if relationship alternation inside a variable-length pattern is
# rejected: run the single-type variants.
Q1_CIRCULAR_SUPPORT_FALLBACK = """
MATCH path = (c:Claim)-[:DERIVED_FROM*2..5]->(c)
RETURN c.id AS claim_id,
       c.text AS claim,
       [n IN nodes(path) | n.text] AS cycle_path,
       length(path) AS cycle_length
"""

# --------------------------------------------------------------------- Q2
# No trusted source and no derivation: an unsupported root claim.
Q2_UNSUPPORTED_CLAIMS = """
MATCH (c:Claim {status: 'active'})
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(s:Source)
  WHERE s.trust_level <> 'unreliable'
WITH c, count(s) AS source_count
OPTIONAL MATCH (c)-[:DERIVED_FROM]->(p:Claim)
WITH c, source_count, count(p) AS parent_count
WHERE source_count = 0 AND parent_count = 0
RETURN c.id AS claim_id, c.text AS claim, c.fingerprint, c.created_at
ORDER BY c.created_at
"""

# --------------------------------------------------------------------- Q3
# Two agents holding incompatible beliefs.
Q3_CONTRADICTIONS = """
MATCH (c1:Claim)-[:CONTRADICTS]->(c2:Claim)
MATCH (a1:Agent)-[:ASSERTS]->(c1)
MATCH (a2:Agent)-[:ASSERTS]->(c2)
RETURN c1.id AS claim_a, c1.text AS text_a, a1.name AS agent_a,
       c2.id AS claim_b, c2.text AS text_b, a2.name AS agent_b,
       c2.created_at AS since
"""

# --------------------------------------------------------------------- Q4
# Severity is a property of graph reach, not of an LLM's opinion.
Q4_CASCADE_REACH = """
MATCH (root:Claim {id: $root_claim_id})
OPTIONAL MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root)
WITH root, collect(DISTINCT down) AS downs
WITH root, downs, downs + [root] AS members
UNWIND members AS member
OPTIONAL MATCH (a:Agent)-[:ASSERTS]->(member)
RETURN root.id AS root_id,
       root.text AS root_text,
       size(downs) AS infected_claims,
       count(DISTINCT a) AS exposed_agents
"""

# --------------------------------------------------------------------- Q5
# Trust is derived from association, so it cannot drift out of sync.
#
# Counts two distinct kinds of exposure: asserting a claim that is itself
# infected, and asserting a claim *derived from* an infected root. The second
# matters, an agent that builds a plan on someone else's falsehood has
# propagated it, and the cascade should cost them too.
Q5_TRUST_DECAY = """
MATCH (a:Agent)
OPTIONAL MATCH (a)-[:ASSERTS]->(c:Claim)<-[:TARGETS]-(i1:Infection)
  WHERE i1.status IN ['active', 'contained']
OPTIONAL MATCH (a)-[:ASSERTS]->(d:Claim)-[:DERIVED_FROM*1..5]->(:Claim)<-[:TARGETS]-(i2:Infection)
  WHERE i2.status IN ['active', 'contained']
WITH a, collect(DISTINCT i1) + collect(DISTINCT i2) AS raw
WITH a, [x IN raw WHERE x IS NOT NULL] AS infs
RETURN a.name AS agent,
       a.base_trust AS base_trust,
       size(infs) AS active_infections,
       a.base_trust - (0.15 * size(infs)) AS effective_trust
ORDER BY effective_trust ASC
"""

# --------------------------------------------------------------------- Q6
# The payoff beat: exact repeat of a previously infected claim.
Q6_IMMUNITY_RECALL = """
MATCH (past:Claim {fingerprint: $fingerprint})<-[:TARGETS]-(i:Infection)
RETURN past.id AS past_claim_id,
       past.text AS past_text,
       past.status AS past_status,
       i.id AS infection_id,
       i.type AS infection_type,
       i.detected_at AS first_seen,
       i.resolved_at AS contained_at
ORDER BY i.detected_at DESC
"""

# --------------------------------------------------------------------- Q7
# Semantic recall of a paraphrase, via vector index over Claim.embedding.
#
# Two things verified empirically against a live instance:
#   1. The query vector MUST be wrapped in vecf32(...). Passing a bare list or
#      a stored property raises "Invalid arguments for procedure".
#   2. The yielded `score` is a cosine DISTANCE (0.0 = identical), despite the
#      option being named similarityFunction. Ordering DESC returns the least
#      similar candidates first, a silent correctness bug at scale.
Q7_VECTOR_RECALL = """
CALL db.idx.vector.queryNodes('Claim', 'embedding', $k, vecf32($query_embedding))
YIELD node AS candidate, score
MATCH (candidate)<-[:TARGETS]-(i:Infection)
RETURN candidate.id AS claim_id,
       candidate.text AS claim,
       candidate.status,
       score AS cosine_distance,
       i.type AS infection_type
ORDER BY score ASC
"""

# Dependency-free paraphrase fallback. The candidate set comes from the graph
# (claims that carry an infection); only the scoring happens in Python.
Q7B_RECALL_CANDIDATES = """
MATCH (past:Claim)<-[:TARGETS]-(i:Infection)
WHERE past.status IN ['quarantined', 'retracted']
RETURN past.id AS claim_id,
       past.text AS claim,
       past.fingerprint AS fingerprint,
       past.status AS status,
       i.id AS infection_id,
       i.type AS infection_type,
       i.detected_at AS first_seen
ORDER BY i.detected_at DESC
"""

# --------------------------------------------------------------------- Q8
# Turns "the swarm develops immunity" into a measurement.
Q8_ANTIBODY_EFFECTIVENESS = """
MATCH (ab:Antibody)-[:NEUTRALIZES]->(i:Infection)
RETURN i.type AS infection_type,
       count(i) AS outbreaks,
       sum(CASE WHEN ab.outcome = 'effective' THEN 1 ELSE 0 END) AS contained,
       sum(CASE WHEN ab.outcome = 'ignored' THEN 1 ELSE 0 END) AS recurred
ORDER BY outbreaks DESC
"""

# --------------------------------------------------------------------- kernel
# The kernel of truth. Antibodies write to it; the sanitiser reads it. Every
# query here is a traversal over provenance, which is why the kernel lives in
# the graph rather than in a side table.

# K1, the active kernel, with provenance.
K1_KERNEL_FACTS = """
MATCH (k:KernelFact)
WHERE k.status = 'active'
OPTIONAL MATCH (k)-[:DERIVED_FROM]->(s:Source)
RETURN k.id AS fact_id,
       k.text AS text,
       k.trust_level AS trust_level,
       k.version AS version,
       k.created_at AS created_at,
       s.name AS source
ORDER BY k.created_at
"""

# K2, which claims currently conflict with the kernel, and how strongly.
K2_KERNEL_CONFLICTS = """
MATCH (c:Claim)-[r:CONFLICTS_WITH]->(k:KernelFact)
RETURN c.id AS claim_id,
       c.text AS claim,
       c.status AS claim_status,
       k.id AS fact_id,
       k.text AS fact,
       k.trust_level AS trust_level,
       r.score AS overlap
ORDER BY r.score DESC
"""

# K3, kernel health in one row: facts held, claims aligned, claims in conflict.
#     This is the number that shows immunity accumulating across outbreaks.
#
#     count(rel) rather than count(*): under OPTIONAL MATCH an unmatched pattern
#     still yields one row of nulls, so count(*) reports 1 for an empty edge set
#     and the dashboard cheerfully reports conflicts that do not exist.
K3_KERNEL_STATS = """
MATCH (k:KernelFact)
WHERE k.status = 'active'
WITH count(k) AS facts
OPTIONAL MATCH (:Claim)-[al:ALIGNED_WITH]->(:KernelFact)
WITH facts, count(al) AS aligned_edges
OPTIONAL MATCH (:Claim)-[cf:CONFLICTS_WITH]->(:KernelFact)
RETURN facts, aligned_edges, count(cf) AS conflict_edges
"""

# K4, claims the sanitiser has not yet judged. Drives the incremental pass.
K4_UNSANITISED_CLAIMS = """
MATCH (c:Claim)
WHERE c.status = 'active' AND c.verdict IS NULL
OPTIONAL MATCH (m:Message)-[:MENTIONS]->(c)
RETURN c.id AS claim_id, c.text AS text, c.created_at AS created_at
ORDER BY c.created_at
LIMIT $limit
"""

# K5, provenance path for one fact: who established it, citing what.
#      This is the "explain this truth" view.
K5_FACT_PROVENANCE = """
MATCH (k:KernelFact {id: $fact_id})
OPTIONAL MATCH (ab:Antibody)-[:ESTABLISHES]->(k)
OPTIONAL MATCH (k)-[:DERIVED_FROM]->(s:Source)
OPTIONAL MATCH (i:Infection)-[:NEUTRALIZES|TARGETS*1..2]-(ab)
RETURN k.id AS fact_id, k.text AS text, k.trust_level AS trust_level,
       k.created_at AS created_at,
       ab.id AS established_by, s.name AS source, s.url AS source_url,
       collect(DISTINCT i.id) AS related_infections
"""


# --------------------------------------------------------------------- helpers
# Used by the UI and the orchestrator, not part of the detection core.

ACTIVE_INFECTIONS = """
MATCH (i:Infection)-[:TARGETS]->(c:Claim)
WHERE i.status IN ['active', 'contained']
OPTIONAL MATCH (i)-[:SPREAD_TO]->(spread:Claim)
RETURN i.id AS infection_id,
       i.type AS type,
       i.severity AS severity,
       i.status AS status,
       c.id AS root_claim_id,
       c.text AS root_claim,
       count(DISTINCT spread) AS spread_count,
       i.detected_at AS detected_at
ORDER BY i.severity DESC, i.detected_at DESC
"""

GRAPH_SNAPSHOT = """
MATCH (n)
OPTIONAL MATCH (n)-[r]->(m)
RETURN n, labels(n) AS n_labels, r, type(r) AS r_type, m, labels(m) AS m_labels
"""

CLAIM_NEIGHBOURHOOD = """
MATCH (c:Claim {id: $claim_id})
OPTIONAL MATCH (c)-[r]-(other)
RETURN c.id AS claim_id, c.text AS claim_text, c.status AS status,
       type(r) AS relation, labels(other) AS other_labels,
       coalesce(other.text, other.name, other.id) AS other_label
"""


QUERIES: dict[str, QuerySpec] = {
    spec.name: spec
    for spec in [
        QuerySpec(
            "Q1_CIRCULAR_SUPPORT",
            Q1_CIRCULAR_SUPPORT,
            "Cycles in SUPPORTED_BY/DERIVED_FROM, self-justifying beliefs.",
        ),
        QuerySpec(
            "Q1B_CIRCULAR_FALLBACK",
            Q1_CIRCULAR_SUPPORT_FALLBACK,
            "Cycle detection restricted to DERIVED_FROM, if alternation is unsupported.",
        ),
        QuerySpec(
            "Q2_UNSUPPORTED_CLAIMS",
            Q2_UNSUPPORTED_CLAIMS,
            "Active claims with no trusted source and no derivation.",
        ),
        QuerySpec(
            "Q3_CONTRADICTIONS",
            Q3_CONTRADICTIONS,
            "Contradiction clusters between two asserting agents.",
        ),
        QuerySpec(
            "Q4_CASCADE_REACH",
            Q4_CASCADE_REACH,
            "Downstream reach of a root claim; the basis of severity.",
            required_params=("root_claim_id",),
        ),
        QuerySpec(
            "Q5_TRUST_DECAY",
            Q5_TRUST_DECAY,
            "Trust derived from unresolved infection exposure.",
        ),
        QuerySpec(
            "Q6_IMMUNITY_RECALL",
            Q6_IMMUNITY_RECALL,
            "Exact repeat of a previously infected claim, by fingerprint.",
            required_params=("fingerprint",),
        ),
        QuerySpec(
            "Q7_VECTOR_RECALL",
            Q7_VECTOR_RECALL,
            "Semantic recall of a paraphrase via the vector index.",
            required_params=("query_embedding", "k"),
            optional=True,
        ),
        QuerySpec(
            "Q7B_RECALL_CANDIDATES",
            Q7B_RECALL_CANDIDATES,
            "Graph-sourced candidate set for dependency-free paraphrase scoring.",
        ),
        QuerySpec(
            "Q8_ANTIBODY_EFFECTIVENESS",
            Q8_ANTIBODY_EFFECTIVENESS,
            "Outbreak/containment counts per infection type.",
        ),
        QuerySpec(
            "K1_KERNEL_FACTS",
            K1_KERNEL_FACTS,
            "The active kernel of truth, with provenance.",
        ),
        QuerySpec(
            "K2_KERNEL_CONFLICTS",
            K2_KERNEL_CONFLICTS,
            "Claims currently in conflict with the kernel.",
        ),
        QuerySpec(
            "K3_KERNEL_STATS",
            K3_KERNEL_STATS,
            "Kernel health: facts held, aligned claims, conflicting claims.",
        ),
        QuerySpec(
            "K4_UNSANITISED_CLAIMS",
            K4_UNSANITISED_CLAIMS,
            "Active claims the sanitiser has not yet judged.",
            required_params=("limit",),
        ),
        QuerySpec(
            "K5_FACT_PROVENANCE",
            K5_FACT_PROVENANCE,
            "Who established a fact and citing what.",
            required_params=("fact_id",),
        ),
        QuerySpec("ACTIVE_INFECTIONS", ACTIVE_INFECTIONS, "Dashboard infection list."),
        QuerySpec("GRAPH_SNAPSHOT", GRAPH_SNAPSHOT, "Full graph for visualisation."),
        QuerySpec(
            "CLAIM_NEIGHBOURHOOD",
            CLAIM_NEIGHBOURHOOD,
            "One-hop explanation for a single claim.",
            required_params=("claim_id",),
        ),
    ]
}
