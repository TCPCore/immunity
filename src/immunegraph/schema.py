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

# Source trust levels.
TRUST_PRIMARY = "primary"
TRUST_REPUTABLE = "reputable"
TRUST_UNKNOWN = "unknown"
TRUST_UNRELIABLE = "unreliable"
