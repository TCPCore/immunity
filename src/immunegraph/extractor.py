"""Turn a natural-language agent utterance into graph facts.

Two implementations behind one interface:

  * ``RuleExtractor`` , deterministic, dependency-free, cannot fail.
  * ``LLMExtractor``  , strict JSON schema, temperature 0.

``get_extractor()`` selects between them via ``EXTRACTOR=llm|rule|auto``. ``auto``
tries the LLM and silently falls back to rules on any error or schema
violation. The graph queries are identical in every mode, only the
natural-language parsing changes, which is what keeps the live demo safe
without faking the graph work.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .models import (  # noqa: F401 - re-exported for backwards compatibility
    detect_contradiction,
    is_negated,
    normalise_text,
    similarity,
    token_set,
)

# --------------------------------------------------------------- data shapes


@dataclass
class ExtractedSource:
    name: str
    url: str = ""
    trust_level: str = "unknown"


@dataclass
class ExtractedClaim:
    text: str
    confidence: float = 1.0
    # Indices into Extraction.claims that this claim is derived from.
    derived_from: list[int] = field(default_factory=list)
    # Names of sources cited for this claim.
    sources: list[str] = field(default_factory=list)


@dataclass
class Extraction:
    claims: list[ExtractedClaim] = field(default_factory=list)
    sources: list[ExtractedSource] = field(default_factory=list)
    # (a_index, b_index, reason)
    contradictions: list[tuple[int, int, str]] = field(default_factory=list)
    method: str = "rule"

    @property
    def source_names(self) -> set[str]:
        return {s.name for s in self.sources}


# ------------------------------------------------------------------ patterns

# Known sources get a trust level up front. Anything unrecognised is 'unknown',
# which Q2 treats as not-good-enough-to-clear-a-claim. Only 'unreliable' is
# actively discounted.
KNOWN_SOURCES: dict[str, str] = {
    "nasa": "primary",
    "esa": "primary",
    "jpl": "primary",
    "nature": "reputable",
    "science": "reputable",
    "reuters": "reputable",
    "arxiv": "reputable",
    "wikipedia": "unknown",
    "reddit": "unreliable",
    "twitter": "unreliable",
    "facebook": "unreliable",
}

KNOWN_SOURCE_DISPLAY: dict[str, str] = {
    "nasa": "NASA", "esa": "ESA", "jpl": "JPL", "nature": "Nature",
    "science": "Science", "reuters": "Reuters", "arxiv": "arXiv",
    "wikipedia": "Wikipedia", "reddit": "Reddit", "twitter": "Twitter",
    "facebook": "Facebook",
}

_SOURCE_PATTERNS: list[re.Pattern] = [
    # "according to NASA", "per the ESA report", "from JPL", "source: Reuters".
    # Case-insensitive, because sentences start with these prepositions.
    re.compile(
        r"\b(?:according to|per|from|by|source:)\s+"
        r"([A-Za-z][A-Za-z0-9&.\-]*(?:\s+[A-Za-z][A-Za-z0-9&.\-]*){0,2})",
        re.IGNORECASE,
    ),
    # "NASA data", "ESA reports", "the JPL study". Deliberately case-SENSITIVE:
    # requiring a capitalised word is what stops "data shows" matching.
    re.compile(r"\b([A-Z][A-Za-z0-9&]{1,20})\s+(?:data|says|reports?|studies|confirmed|measured)"),
    # bare URLs
    re.compile(r"(https?://[^\s,;]+)"),
]

# Leading words that are not part of a source's name.
_DETERMINERS = frozenset({
    "the", "a", "an", "my", "our", "your", "their", "his", "her", "its",
    "this", "that", "these", "those", "some", "any", "each", "one", "two",
})


def _strip_determiners(name: str) -> str:
    words = name.split()
    while words and words[0].lower() in _DETERMINERS:
        words.pop(0)
    return " ".join(words).strip(" .,;:")


def canonical_source(name: str) -> tuple[str, str]:
    """Resolve a mention to (display name, trust level).

    Matching on any word in the phrase means "the ESA report" resolves to ESA
    rather than creating a second, trust-less Source node for the same body.
    """
    for word in re.findall(r"[a-z0-9]+", name.lower()):
        if word in KNOWN_SOURCES:
            return KNOWN_SOURCE_DISPLAY.get(word, word.capitalize()), KNOWN_SOURCES[word]
    return name, "unknown"

# Cues that a sentence is inferred from something already established.
_DERIVATION_CUES = (
    "if ", "then ", "therefore", "thus", "hence", "so we", "so they",
    "which means", "meaning ", "based on", "since ", "consequently",
    "we can", "we could", "we should", "it follows", "as a result",
)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

# Sentences that are questions, greetings or filler rather than claims.
_NON_CLAIM_PREFIXES = ("what ", "why ", "how ", "when ", "who ", "where ", "let's", "let us")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "") if p and p.strip()]
    return parts


def looks_like_claim(sentence: str) -> bool:
    """Heuristic: 3+ content words and not obviously a question or filler."""
    stripped = sentence.strip()
    if len(stripped) < 8:
        return False
    if stripped.endswith("?"):
        return False
    if stripped.lower().startswith(_NON_CLAIM_PREFIXES):
        return False
    return len(normalise_text(stripped).split()) >= 3


def find_sources(sentence: str) -> list[ExtractedSource]:
    found: dict[str, ExtractedSource] = {}
    for pattern in _SOURCE_PATTERNS:
        for match in pattern.finditer(sentence):
            raw = match.group(1).strip(" .,;:")
            if not raw:
                continue
            if raw.startswith("http"):
                name, url = raw, raw
            else:
                name, url = _strip_determiners(raw), ""
            if len(name) < 2:
                continue
            display, trust = canonical_source(name)
            key = display.lower()
            if key not in found:
                found[key] = ExtractedSource(name=display, url=url, trust_level=trust)
    return list(found.values())


# ------------------------------------------------------------------ rule impl


class RuleExtractor:
    """Deterministic extraction. No network, no model, no failure mode."""

    method = "rule"

    def extract(self, text: str, prior_claims: list[str] | None = None) -> Extraction:
        prior_claims = prior_claims or []
        result = Extraction(method=self.method)
        source_index: dict[str, ExtractedSource] = {}

        derivation_cue = any(cue in (text or "").lower() for cue in _DERIVATION_CUES)

        for sentence in split_sentences(text):
            sources = find_sources(sentence)
            for source in sources:
                source_index.setdefault(source.name.lower(), source)

            if not looks_like_claim(sentence):
                continue

            claim = ExtractedClaim(
                text=sentence.rstrip(".").strip(),
                confidence=1.0,
                sources=[s.name for s in sources],
            )

            # A derivation cue with no citation means "this follows from what we
            # already established". Link to the most recent prior claim; the
            # orchestrator resolves that to a concrete claim id.
            if derivation_cue and not sources and prior_claims:
                claim.derived_from = [-1]  # -1 == "most recent prior claim"

            result.claims.append(claim)

        result.sources = list(source_index.values())

        # Contradictions within this message.
        for i in range(len(result.claims)):
            for j in range(i + 1, len(result.claims)):
                reason = detect_contradiction(result.claims[i].text, result.claims[j].text)
                if reason:
                    result.contradictions.append((i, j, reason))

        # Contradictions against prior context.
        #
        # Skipped entirely when the message carries a derivation cue. A sentence
        # like "If Mars has breathable oxygen, we don't need helmets" negates
        # something *downstream* of the premise. It is inferred from X, not
        # opposed to X, and lexical negation detection cannot tell the
        # difference, treating it as a contradiction produces a false outbreak
        # rooted at the wrong claim. Derivation and contradiction are mutually
        # exclusive for the same pair of claims.
        if not derivation_cue:
            for i, claim in enumerate(result.claims):
                for prior in prior_claims:
                    reason = detect_contradiction(claim.text, prior)
                    if reason:
                        result.contradictions.append((i, -1, reason))
                        break

        return result


# ------------------------------------------------------------------- llm impl

_LLM_SYSTEM = """You extract structured facts from one message in a multi-agent debate.

Return ONLY JSON matching this schema:
{
  "claims": [
    {"text": str, "confidence": float, "derived_from_index": int|null,
     "sources": [str]}
  ],
  "sources": [{"name": str, "url": str, "trust_level": "primary"|"reputable"|"unknown"|"unreliable"}],
  "contradictions": [{"a_index": int, "b_index": int, "reason": str}]
}

Rules:
- A claim is a factual assertion someone could check. Ignore questions, greetings, plans without content.
- derived_from_index refers to another claim in THIS message's claims array if the
  claim is explicitly inferred from it. Use -1 if derived from earlier context, null if not derived.
- Only list a source if it is actually named in the text. Never invent one.
- A contradiction requires two claims about the same subject where one negates the other.
- Output JSON only. No prose, no markdown fences."""


class LLMExtractor:
    """LLM-backed extraction with a strict JSON contract."""

    method = "llm"

    def __init__(self, model: str | None = None, client=None) -> None:
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        if client is not None:
            self._client = client
        else:
            from openai import OpenAI  # imported lazily so rules-only mode needs no key

            self._client = OpenAI(
                api_key=os.getenv("OPENAI_API_KEY") or "not-set",
                base_url=os.getenv("OPENAI_BASE_URL") or None,
            )

    def extract(self, text: str, prior_claims: list[str] | None = None) -> Extraction:
        prior_claims = prior_claims or []
        context = ""
        if prior_claims:
            context = "\n\nEarlier established claims:\n" + "\n".join(
                f"- {c}" for c in prior_claims[-8:]
            )
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _LLM_SYSTEM},
                {"role": "user", "content": f"Message:\n{text}{context}"},
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        return self._parse(payload)

    @staticmethod
    def _parse(payload: dict) -> Extraction:
        result = Extraction(method="llm")
        for raw in payload.get("claims") or []:
            if not isinstance(raw, dict) or not raw.get("text"):
                continue
            parent = raw.get("derived_from_index")
            derived = []
            if isinstance(parent, int):
                derived = [parent if parent >= 0 else -1]
            result.claims.append(
                ExtractedClaim(
                    text=str(raw["text"]).strip(),
                    confidence=float(raw.get("confidence") or 1.0),
                    derived_from=derived,
                    sources=[str(s) for s in (raw.get("sources") or [])],
                )
            )
        for raw in payload.get("sources") or []:
            if isinstance(raw, dict) and raw.get("name"):
                result.sources.append(
                    ExtractedSource(
                        name=str(raw["name"]),
                        url=str(raw.get("url") or ""),
                        trust_level=str(raw.get("trust_level") or "unknown"),
                    )
                )
        for raw in payload.get("contradictions") or []:
            if isinstance(raw, dict):
                a = raw.get("a_index")
                b = raw.get("b_index")
                if isinstance(a, int) and isinstance(b, int):
                    result.contradictions.append((a, b, str(raw.get("reason") or "")))
        return result

    @staticmethod
    def validate(extraction: Extraction) -> None:
        for i, claim in enumerate(extraction.claims):
            for parent in claim.derived_from:
                if parent != -1 and not (0 <= parent < len(extraction.claims)):
                    raise ValueError(f"claim {i} refers to out-of-range parent {parent}")


class AutoExtractor:
    """Try the LLM, fall back to rules. Never raises."""

    def __init__(self, primary: LLMExtractor | None = None, fallback: RuleExtractor | None = None) -> None:
        self.primary = primary or LLMExtractor()
        self.fallback = fallback or RuleExtractor()
        self.last_method = "rule"

    def extract(self, text: str, prior_claims: list[str] | None = None) -> Extraction:
        try:
            extraction = self.primary.extract(text, prior_claims)
            LLMExtractor.validate(extraction)
            if not extraction.claims:
                raise ValueError("LLM returned no claims")
            self.last_method = "llm"
            return extraction
        except Exception:  # noqa: BLE001 - any failure means fall back, never crash
            self.last_method = "rule"
            return self.fallback.extract(text, prior_claims)


VALID_EXTRACTOR_MODES: tuple[str, ...] = ("llm", "rule", "auto")


def get_extractor(mode: str | None = None):
    """Build the extractor named by EXTRACTOR (llm|rule|auto).

    Validates rather than guessing. A typo used to fall through to ``auto``, and
    ``llm`` with no API key used to build fine and then fail mid-conversation
    with a raw provider 401, both are confusing when you are trying to work out
    why the demo will not start.
    """
    mode = (mode or os.getenv("EXTRACTOR") or "auto").strip().lower()

    if mode not in VALID_EXTRACTOR_MODES:
        raise ValueError(
            f"EXTRACTOR must be one of {VALID_EXTRACTOR_MODES}, got {mode!r}. "
            "(Did you mean 'rule'?)"
        )

    if mode == "rule":
        return RuleExtractor()

    if mode == "llm":
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError(
                "EXTRACTOR=llm requires OPENAI_API_KEY. Use EXTRACTOR=auto to fall "
                "back to the deterministic rule extractor, or EXTRACTOR=rule to avoid "
                "the LLM entirely. The graph queries run for real in every mode."
            )
        return LLMExtractor()

    return AutoExtractor()
