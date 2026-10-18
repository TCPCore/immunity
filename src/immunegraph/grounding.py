"""Ground a visitor's claim in public knowledge.

The kernel of truth previously grew only from antibody corrections, which meant
the swarm could only learn about things it had already been told were wrong.
This closes that loop: anyone can submit a claim and have it checked against a
public source, so the *same* immune machinery, infection, antibody, kernel fact
- can arrest something it was never briefed on.

Wikipedia is the source because it is free, needs no API key, returns a citable
URL, and is unambiguously public data (hackathon rule 6).

**Honest limits.** This is lexical grounding, not semantic verification. Two
design decisions keep it trustworthy rather than merely confident:

*Subject check.* Wikipedia's search matches individual words, so "Mars is made
of cheese" returns articles about cheese. An article is only considered if its
*title* shares a token with the claim, no cheese article is called anything with
"Mars" in it, so that claim comes back inconclusive instead of wrongly
corroborated.

*Sentence-level, Jaccard-scored agreement.* Judging a whole article at once fires
on unrelated text, and the overlap coefficient flatters short claims, two shared
words out of three reads as 0.67. An earlier version of this module reported
"Mars is made of cheese" as SUPPORTED by an article about Cheez-Its for exactly
that reason. Agreement now needs real Jaccard density within a single sentence.

It still cannot refute a claim whose contradiction is phrased in entirely
different vocabulary, and when it cannot decide it says so rather than guessing.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .models import conflicts_with_fact, is_negated, overlap_coefficient, token_set

WIKI_API = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "ImmuneGraph/1.0 (hackathon demo; https://github.com/TCPCore/immunity)"

# Words that limit or exclude rather than negate outright. "Trace levels of
# oxygen" refutes "breathable oxygen" without containing the word "not".
#
# "only" is deliberately absent. It is far too common, "the Moon is Earth's
# only natural satellite" refuted the true claim that the Moon orbits the Earth.
# A false refutation is worse than an inconclusive answer, so the list holds
# only words that genuinely exclude a quantity or a capability.
LIMITERS: tuple[str, ...] = (
    "trace", "no ", "not ", "never", "lack", "lacks", "absence",
    "absent", "cannot", "insufficient", "negligible", "nearly none", "almost no",
    "unbreathable", "thinner", "less than", "as little as", "virtually none",
    "devoid of", "free of",
)

CONTRADICT_OVERLAP = 0.45
# A claim is supported when the sentence contains essentially everything it
# asserts. Jaccard punishes a long sentence for its extra words, so "Earth
# orbits the Sun" stayed inconclusive against a sentence saying exactly that.
SUPPORT_COVERAGE = 0.85

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Evidence:
    """What a public source says about a claim."""

    claim: str
    query: str
    title: str | None = None
    url: str | None = None
    extract: str | None = None
    sentence: str = ""
    verdict: str = "silent"  # 'contradicts' | 'supports' | 'silent'
    overlap: float = 0.0
    reason: str = ""
    error: str | None = None

    @property
    def is_false(self) -> bool:
        return self.verdict == "contradicts"

    def fact_text(self, limit: int = 200) -> str | None:
        """A short, claim-shaped form of the evidence, for the kernel."""
        source = self.sentence or self.extract
        if not source:
            return None
        # Split on sentence boundaries, not on every full stop: a decimal like
        # "96.5% carbon dioxide" has no space after the point, and splitting
        # naively truncated it to "Venus is 96".
        first = _SENTENCE.split(source.strip())[0].strip().rstrip(".!?")
        if not first:
            return None
        if len(first) > limit:
            first = first[: limit - 1].rstrip() + "..."
        return first

    def describe(self) -> str:
        if self.error:
            return f"grounding failed: {self.error}"
        if self.verdict == "silent":
            return f"inconclusive - closest public source: {self.title or 'none'}"
        return f"{self.verdict} by {self.title!r} (overlap {self.overlap:.2f})"


def _get(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return json.load(urllib.request.urlopen(request, timeout=timeout))


def _query_terms(claim: str, limit: int = 6) -> str:
    """Search terms for a claim: its content words, in order, deduplicated."""
    seen: list[str] = []
    for token in claim.lower().split():
        cleaned = re.sub(r"[^a-z0-9]", "", token)
        if len(cleaned) < 3 or cleaned in seen:
            continue
        seen.append(cleaned)
        if len(seen) >= limit:
            break
    return " ".join(seen)


class WikipediaGrounder:
    """Checks a claim against Wikipedia's public API. No key, no cost."""

    def __init__(self, timeout: float = 12.0, limit: int = 3) -> None:
        self.timeout = timeout
        self.limit = limit

    # ---------------------------------------------------------------- fetching

    def search(self, query: str, hits: int | None = None) -> list[str]:
        count = hits or self.limit
        url = (
            f"{WIKI_API}?action=query&format=json&list=search"
            f"&srsearch={urllib.parse.quote(query)}&srlimit={count}"
        )
        payload = _get(url, self.timeout)
        return [hit["title"] for hit in payload.get("query", {}).get("search", [])]

    def extract(self, title: str) -> str | None:
        url = (
            f"{WIKI_API}?action=query&format=json&prop=extracts"
            f"&explaintext=1&exintro=1&redirects=1"
            f"&titles={urllib.parse.quote(title)}"
        )
        payload = _get(url, self.timeout)
        for page in payload.get("query", {}).get("pages", {}).values():
            text = (page.get("extract") or "").strip()
            if text:
                return text
        return None

    # ----------------------------------------------------------------- judging

    @staticmethod
    def judge(claim: str, extract: str) -> tuple[str, float, str, str]:
        """Judge the claim sentence by sentence.

        Returns (verdict, score, reason, evidence_sentence).
        """
        claim_tokens = token_set(claim)
        if not claim_tokens:
            return "silent", 0.0, "the claim has no content words", ""

        best: tuple[float, str] = (0.0, "")
        for sentence in _SENTENCE.split(extract):
            sentence = sentence.strip()
            if not sentence:
                continue
            sentence_tokens = token_set(sentence)
            shared = claim_tokens & sentence_tokens
            if not shared:
                continue

            overlap = overlap_coefficient(claim, sentence)

            # 1. A limiter in the same sentence as a shared term.
            if not is_negated(claim) and any(
                limiter in sentence.lower() for limiter in LIMITERS
            ):
                if overlap >= CONTRADICT_OVERLAP:
                    return (
                        "contradicts", overlap,
                        "the source limits what the claim asserts", sentence,
                    )

            # 2. Explicit negation on one side only.
            if conflicts_with_fact(claim, sentence) is not None:
                return "contradicts", overlap, "negation asymmetry against the source", sentence

            # 3. Agreement: the sentence must contain essentially everything the
            #    claim asserts. Coverage, not Jaccard, a sentence that says
            #    exactly what the claim says is still long, and Jaccard reads
            #    that length as disagreement.
            coverage = len(shared) / len(claim_tokens)
            if coverage >= SUPPORT_COVERAGE and len(claim_tokens) >= 2:
                return (
                    "supports", coverage,
                    "the source states substantially the same thing", sentence,
                )

            if overlap > best[0]:
                best = (overlap, sentence)

        return "silent", best[0], "the source mentions the subject but not the assertion", best[1]

    # -------------------------------------------------------------------- api

    def ground(self, claim: str, hits: int = 5) -> Evidence:
        """Search, keep only articles about the claim's subject, then judge."""
        query = _query_terms(claim)
        evidence = Evidence(claim=claim, query=query)
        if not query:
            evidence.reason = "no searchable terms in the claim"
            return evidence

        try:
            titles = self.search(query, hits=hits)
        except Exception as exc:  # noqa: BLE001 - network is best-effort
            evidence.error = f"search failed: {type(exc).__name__}"
            return evidence

        if not titles:
            evidence.reason = "no public article matched"
            return evidence

        claim_tokens = token_set(claim)
        candidates: list[Evidence] = []

        for title in titles:
            # The subject check. Without it, "Mars is made of cheese" gets judged
            # against an article about Cheez-Its and comes back corroborated.
            if not (claim_tokens & token_set(title)):
                continue
            try:
                extract = self.extract(title)
            except Exception as exc:  # noqa: BLE001
                evidence.error = f"extract failed: {type(exc).__name__}"
                continue
            if not extract:
                continue

            verdict, score, reason, sentence = self.judge(claim, extract)
            candidate = Evidence(
                claim=claim,
                query=query,
                title=title,
                url="https://en.wikipedia.org/wiki/"
                + urllib.parse.quote(title.replace(" ", "_")),
                extract=extract,
                sentence=sentence,
                verdict=verdict,
                overlap=score,
                reason=reason,
            )
            if verdict == "contradicts":
                return candidate
            candidates.append(candidate)

        if not candidates:
            evidence.reason = "no article was about the claim's subject"
            return evidence

        candidates.sort(key=lambda c: (c.verdict == "supports", c.overlap), reverse=True)
        return candidates[0]
