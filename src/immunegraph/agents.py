"""The simulated swarm: personas and the scripted Mars mission.

The personas exist so the demo has distinct trust profiles and roles. Nothing
here is required for the immune system to work, a real deployment would take
agent names from whatever framework is driving the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    base_trust: float
    blurb: str = ""


RESEARCHER = Persona(
    id="ag_researcher",
    name="Researcher",
    role="researcher",
    base_trust=1.0,
    blurb="Surfaces findings. In this scenario, without a source.",
)

ANALYST = Persona(
    id="ag_analyst",
    name="Analyst",
    role="analyst",
    base_trust=1.0,
    blurb="Reasons from whatever the Researcher established.",
)

PLANNER = Persona(
    id="ag_planner",
    name="Planner",
    role="planner",
    base_trust=1.0,
    blurb="Turns conclusions into operational plans.",
)

# The immune system acts in the graph too, so it is modelled as an agent.
ANTIBODY_SYSTEM = Persona(
    id="agent_antibody",
    name="Antibody System",
    role="immune",
    base_trust=1.0,
    blurb="Dispatched on detection. Injects sourced corrections.",
)

DEFAULT_SWARM: tuple[Persona, ...] = (RESEARCHER, ANALYST, PLANNER)


# ------------------------------------------------------------------ scenario

# (persona, utterance, note-for-the-narrator)
#
# The first three turns are the cascade. Note that neither the Analyst nor the
# Planner is lying: each reasons correctly from a premise it was handed. That
# is the point of the demo, a swarm can be perfectly internally consistent and
# still wrong.
MARS_CASCADE: tuple[tuple[Persona, str, str], ...] = (
    (
        RESEARCHER,
        "Mars has breathable oxygen at night.",
        "No source cited. This is the unsupported root.",
    ),
    (
        ANALYST,
        "If Mars has breathable oxygen, we don't need helmets.",
        "Derived from the previous claim (hop 1).",
    ),
    (
        PLANNER,
        "We can skip life support.",
        "Derived from the Analyst's claim (hop 2).",
    ),
)

# The immunity test. Deliberately a paraphrase, not an exact repeat, so that
# fingerprint matching alone cannot catch it.
MARS_REPEAT = (
    RESEARCHER,
    "Mars has oxygen at night.",
    "Paraphrase of the original. Immunity recall should fire immediately.",
)

MARS_TOPIC = "Mars mission planning"


def persona_by_name(name: str) -> Persona:
    for persona in (*DEFAULT_SWARM, ANTIBODY_SYSTEM):
        if persona.name.lower() == name.lower():
            return persona
    raise KeyError(name)
