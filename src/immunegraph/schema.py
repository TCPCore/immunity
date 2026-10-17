"""Schema DDL. Must stay in sync with docs/GRAPH_MODEL.md section 4.

Statement order matters only in that indexes require the labels to be
creatable; FalkorDB creates indexes over empty label sets happily.
"""

from __future__ import annotations

# Hot paths: immunity recall by fingerprint, and the active-claim scan by status.
INDEX_STATEMENTS: list[str] = [
    "CREATE INDEX FOR (c:Claim) ON (c.fingerprint)",
    "CREATE INDEX FOR (c:Claim) ON (c.status)",
    "CREATE INDEX FOR (c:Claim) ON (c.id)",
    "CREATE INDEX FOR (a:Agent) ON (a.name)",
    "CREATE INDEX FOR (i:Infection) ON (i.status)",
    "CREATE INDEX FOR (i:Infection) ON (i.id)",
    "CREATE INDEX FOR (s:Session) ON (s.id)",
    "CREATE INDEX FOR (m:Message) ON (m.id)",
    # The kernel is consulted on every sanitisation, so its id lookup is hot.
    "CREATE INDEX FOR (k:KernelFact) ON (k.id)",
    "CREATE INDEX FOR (k:KernelFact) ON (k.status)",
]

# Canonical label and relationship names, used for validation in tests.
NODE_LABELS: list[str] = [
    "Tenant",
    "Session",
    "Message",
    "Agent",
    "Claim",
    "Source",
    "Infection",
    "Antibody",
    "KernelFact",
]

RELATIONSHIP_TYPES: list[str] = [
    "SPOKE",
    "MENTIONS",
    "ASSERTS",
    "SUPPORTED_BY",
    "DERIVED_FROM",
    "CONTRADICTS",
    "REPEATS",
    "TRUSTS",
    "HANDOFF_TO",
    "TARGETS",
    "SPREAD_TO",
    "NEUTRALIZES",
    "INJECTS",
    "CONTAINS",
    # kernel of truth
    "ESTABLISHES",
    "CONFLICTS_WITH",
    "ALIGNED_WITH",
    "SUPERSEDES",
]

# Claim lifecycle states.
CLAIM_ACTIVE = "active"
CLAIM_QUARANTINED = "quarantined"
# Support invalidated: the claim descends from a quarantined root and carries no
# independent trusted source. Not proven false, but no longer trustworthy.
# Without this state a cascade leaves downstream claims reading as 'believed'.
CLAIM_SUSPECT = "suspect"
CLAIM_CORROBORATED = "corroborated"
CLAIM_RETRACTED = "retracted"

# Infection lifecycle states.
INFECTION_ACTIVE = "active"
INFECTION_CONTAINED = "contained"
INFECTION_RESOLVED = "resolved"

# Infection classes.
INFECTION_CIRCULAR = "circular_support"
INFECTION_UNSUPPORTED = "unsupported_root"
INFECTION_CONTRADICTION = "contradiction"
INFECTION_REPEAT = "repeat_offender"
# Refuted by a public source rather than by the support structure.
INFECTION_EXTERNAL = "external_contradiction"

# Source trust levels.
TRUST_PRIMARY = "primary"
TRUST_REPUTABLE = "reputable"
TRUST_UNKNOWN = "unknown"
TRUST_UNRELIABLE = "unreliable"

# --- Kernel of truth -------------------------------------------------------
#
# KernelFacts are canonical, source-backed statements the antibody establishes
# and every new claim is sanitised against. They are the swarm's memory of what
# is *right*, complementing Infection/Claim which record what was *wrong*.
#
# This is deliberately a graph concern, not a side table: a fact's provenance
# (which source, which antibody established it) and its conflicts are edges, and
# sanitisation is a traversal. Move it out of FalkorDB and sanitisation stops
# existing, which is what keeps rule 3 satisfied.

KERNEL_ACTIVE = "active"
KERNEL_SUPERSEDED = "superseded"

# Sanitiser verdicts, stored on the Claim so the UI can badge it without
# re-running the check.
VERDICT_CLEAN = "clean"          # agrees with a kernel fact
VERDICT_CONFLICTS = "conflicts"  # contradicts a kernel fact
VERDICT_UNKNOWN = "unknown"      # kernel has nothing to say about it
