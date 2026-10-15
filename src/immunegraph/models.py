"""Domain models and the claim fingerprinting that makes recall deterministic.

The single most important beat in the demo is immunity recall: a previously
infected claim reappears and is flagged instantly. That must never depend on
an LLM noticing the similarity, so claims carry a normalised fingerprint
computed here, in plain deterministic Python.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Deliberately small: only words that carry no discriminating weight in a
# factual claim. Over-stripping would make distinct claims collide.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "has", "have", "had", "do", "does", "did", "of", "in", "on", "at",
        "to", "for", "with", "and", "or", "but", "if", "then", "than",
        "that", "this", "these", "those", "it", "its", "as", "by", "from",
        "we", "you", "they", "i", "he", "she", "will", "would", "can",
        "could", "should", "may", "might", "must", "there", "here",
    }
)

_WORD_RE = re.compile(r"[a-z0-9]+")

# Expand contractions before tokenising so negations survive normalisation.
# Without this, "don't" tokenises to "don t" and the negation marker "not" is
# lost, which silently breaks contradiction detection.
_CONTRACTIONS: tuple[tuple[str, str], ...] = (
    ("n't", " not"),
    ("cannot", "can not"),
    ("'re", " are"),
    ("'ve", " have"),
    ("'ll", " will"),
)


def _expand_contractions(text: str) -> str:
    for suffix, replacement in _CONTRACTIONS:
        text = text.replace(suffix, replacement)
    return text


def normalise_text(text: str) -> str:
    """Lowercase, strip accents and punctuation, drop stopwords.

    Word order is preserved so that "oxygen has Mars" does not collide with
    "Mars has oxygen". Semantic paraphrases are handled by vector recall
    (query Q7), not by this function.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    ascii_only = _expand_contractions(ascii_only)
    tokens = [t for t in _WORD_RE.findall(ascii_only) if t not in STOPWORDS]
    return " ".join(tokens)


def token_set(text: str) -> set[str]:
    """Normalised token set, used for fallback similarity scoring."""
    return set(normalise_text(text).split())


def similarity(left: str, right: str) -> float:
    """Jaccard similarity over normalised tokens, in [0, 1].

    A dependency-free fallback for paraphrase detection when embeddings are
    unavailable. The candidate set still comes from the graph.
    """
    a, b = token_set(left), token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def fingerprint(text: str) -> str:
    """Stable short hash of the normalised claim text."""
    normalised = normalise_text(text)
    digest = hashlib.sha1(normalised.encode("utf-8")).hexdigest()
    return digest[:16]


def utcnow() -> str:
    """ISO-8601 UTC timestamp, used for every temporal property."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Claim:
    id: str
    text: str
    status: str = "active"
    verification: str = "unsourced"
    confidence: float = 1.0
    created_at: str = field(default_factory=utcnow)

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.text)

    def to_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "verification": self.verification,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }


@dataclass
class Agent:
    id: str
    name: str
    role: str = "participant"
    base_trust: float = 1.0
    tenant_id: str = "default"

    def to_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "base_trust": self.base_trust,
            "tenant_id": self.tenant_id,
        }


@dataclass
class Source:
    id: str
    name: str
    url: str = ""
    trust_level: str = "unknown"

    def to_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "trust_level": self.trust_level,
        }


@dataclass
class Infection:
    id: str
    type: str
    status: str = "active"
    severity: float = 0.0
    detected_at: str = field(default_factory=utcnow)
    resolved_at: str | None = None

    def to_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "severity": self.severity,
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
        }


@dataclass
class Antibody:
    id: str
    correction_text: str
    strategy: str = "source_citation"
    outcome: str = "unknown"
    created_at: str = field(default_factory=utcnow)

    def to_properties(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "correction_text": self.correction_text,
            "strategy": self.strategy,
            "outcome": self.outcome,
            "created_at": self.created_at,
        }


def severity_from_reach(infected_claims: int, exposed_agents: int) -> float:
    """Severity is a function of graph reach, never of an LLM's opinion.

    Deliberately simple and explainable: one point for the root, one per
    downstream claim it contaminated, one per agent exposed.
    """
    return float(1 + infected_claims + exposed_agents)
