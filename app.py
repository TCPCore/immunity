"""ImmuneGraph, Streamlit UI.

Three columns: the conversation, the live belief graph, and the immune
dashboard. Every panel is driven by a Cypher query; nothing is computed in
Python that the graph should be computing.

    streamlit run app.py
"""

from __future__ import annotations

import time

import streamlit as st

st.set_page_config(
    page_title="ImmuneGraph, self-healing memory for agent swarms",
    page_icon="🧬",
    layout="wide",
)

from immunegraph.agents import MARS_CASCADE, MARS_REPEAT, MARS_TOPIC  # noqa: E402
from immunegraph.audit import KernelAuditor  # noqa: E402
from immunegraph.company import COMPANY, DOCUMENTS, CompanyBrain  # noqa: E402
from immunegraph.config import (  # noqa: E402
    STREAMLIT_SECRETS_BRIDGED,
    STREAMLIT_SECRETS_ERROR,
    STREAMLIT_SECRETS_SEEN,
    Settings,
)
from immunegraph.db import GraphStore  # noqa: E402
from immunegraph.event_log import WriteLog  # noqa: E402
from immunegraph.projection import import_jsonl  # noqa: E402
from immunegraph.swarm import Swarm  # noqa: E402

# --------------------------------------------------------------------- palette

NODE_COLOURS = {
    "Agent": "#4C8BF5",
    "Claim": "#2E9E5B",
    # The kernel is deliberately the loudest colour in the graph: it is the
    # swarm's accumulated truth, not another kind of claim.
    "KernelFact": "#7B4DFF",
    "Source": "#9AA0A6",
    "Infection": "#E8453C",
    "Antibody": "#F2B705",
    "Message": "#C9D3E0",
    "Session": "#DCE3EC",
    "Tenant": "#DCE3EC",
}

QUARANTINED_COLOUR = "#7A2E28"
# Support invalidated by an upstream quarantine, not proven false, but no
# longer trustworthy. Amber, deliberately distinct from the red of a refuted claim.
SUSPECT_COLOUR = "#D98324"


# ------------------------------------------------------------------- resources


@st.cache_resource(show_spinner=False)
def get_context() -> tuple[Swarm, GraphStore, WriteLog, Settings]:
    """Build the store, the write-ahead log and the swarm.

    Deliberately no query here. If FalkorDB is unreachable, anything that talks
    to it raises before ``require_db`` gets the chance to explain the problem,
    and a judge who opens the app before starting Docker would see a traceback
    instead of "run docker compose up -d".
    """
    settings = Settings.from_env()
    store = GraphStore(settings)
    # Durability: FalkorDB Cloud's free tier has no persistence, so every write
    # is appended here and the graph can be rebuilt from it.
    log = WriteLog(settings.write_log_path)
    swarm = Swarm(store, log=log)
    return swarm, store, log, settings


def restore_if_empty(swarm: Swarm, store: GraphStore, settings: Settings) -> str | None:
    """Rebuild the graph from the log if the database came back empty.

    This is the durability story actually working: kill the container, restart
    it, reload the app, and the swarm's memory is still there. The log is the
    durable record; the graph is a projection of it.
    """
    if store.node_count() > 0:
        return None

    if swarm.log_count() == 0 and settings.seed_log_path:
        imported = import_jsonl(swarm.log, settings.seed_log_path)
        if imported:
            result = swarm.rebuild_from_log()
            return (
                f"Cold start: replayed {result['applied']} seeded writes from "
                f"`{settings.seed_log_path}`."
            )

    if swarm.log_count() > 0:
        result = swarm.rebuild_from_log()
        if result["applied"]:
            return f"Cold start: rebuilt the graph from {result['applied']} logged writes."
    return None


def require_db(store: GraphStore) -> bool:
    if store.ping():
        return True

    st.error(
        f"Cannot reach FalkorDB at **{store.settings.host}:{store.settings.port}**."
    )
    # Diagnose the most common hosted failure: secrets were never applied, so
    # the app quietly fell back to localhost and looks broken for no visible
    # reason.
    if store.settings.host in {"localhost", "127.0.0.1"}:
        st.warning(
            "`FALKORDB_HOST` is still **localhost**, so no secrets reached the app.\n\n"
            "On Streamlit Community Cloud: **App settings → Secrets**, paste the block "
            "from `docs/DEPLOY.md`, then **Reboot app**. Editing secrets does not take "
            "effect until the app restarts."
        )
    if STREAMLIT_SECRETS_ERROR:
        st.error("The secrets block could not be read, check its TOML syntax:")
        st.code(STREAMLIT_SECRETS_ERROR, language="text")

    st.caption(
        f"secrets bridged: {STREAMLIT_SECRETS_BRIDGED} · "
        f"secret keys seen: {list(STREAMLIT_SECRETS_SEEN) or 'none'} · "
        f"graph `{store.settings.graph}` · extractor `{store.settings.extractor}`"
    )
    if not STREAMLIT_SECRETS_SEEN:
        st.info(
            "The app sees **no secrets at all**. That means the Secrets box is empty, or "
            "it was saved to a different app, or the app has not been rebooted since."
        )
    st.markdown(
        "Locally: `docker compose up -d`, or point `FALKORDB_HOST` in `.env` at a "
        "FalkorDB Cloud instance."
    )
    return False


# ------------------------------------------------------------------ rendering


def render_transcript(swarm: Swarm) -> None:
    rows = swarm.store.rows(
        """
        MATCH (a:Agent)-[:SPOKE]->(m:Message)
        OPTIONAL MATCH (m)-[:MENTIONS]->(c:Claim)
        RETURN a.name, m.text, m.seq, collect(c.status), collect(c.verdict)
        ORDER BY m.seq
        """
    )
    if not rows:
        st.caption("No conversation yet. Run the Mars mission to begin.")
        return
    for name, text, seq, statuses, verdicts in rows:
        statuses = statuses or []
        verdicts = [v for v in (verdicts or []) if v]
        if "quarantined" in statuses:
            badge = "  🚫 *quarantined*"
        elif "suspect" in statuses:
            badge = "  ⚠️ *support invalidated*"
        elif statuses and statuses[0]:
            badge = "  ✅ *believed*"
        else:
            badge = ""
        # The sanitiser's verdict is separate from the cascade verdict: a claim
        # can be structurally supported and still contradict what we know.
        if "conflicts" in verdicts:
            badge += "  🛡️ *contradicts the kernel*"
        elif "clean" in verdicts:
            badge += "  🛡️ *agrees with the kernel*"
        with st.chat_message("assistant" if name != "Antibody System" else "system"):
            st.markdown(f"**{name}**{badge}")
            st.markdown(text)


def _short_label(text: str, words: int = 4, chars: int = 24) -> str:
    """Keep node labels legible; full text stays in the hover tooltip."""
    short = " ".join((text or "").split()[:words])
    if len(short) > chars:
        short = short[: chars - 1].rstrip() + "…"
    return short or (text or "")[:chars]


def render_graph(swarm: Swarm, height: int = 660) -> None:
    snapshot = swarm.graph_snapshot()
    if not snapshot["nodes"]:
        st.info("The graph is empty.")
        return

    try:
        from streamlit_agraph import Config, Edge, Node, agraph
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Graph canvas unavailable ({exc}). Falling back to a table.")
        st.dataframe(snapshot["nodes"], width="stretch")
        return

    nodes = []
    for node in snapshot["nodes"]:
        label = node["label"]
        props = node.get("properties") or {}
        colour = NODE_COLOURS.get(label, "#CCCCCC")
        size = 18
        if label == "Claim":
            if props.get("status") == "quarantined":
                colour = QUARANTINED_COLOUR
                size = 22
            elif props.get("status") == "suspect":
                colour = SUSPECT_COLOUR
                size = 20
        if label == "KernelFact":
            # Larger than a claim: the kernel is what survived scrutiny.
            size = 26
        if label == "Infection":
            size = 18 + min(float(props.get("severity") or 0), 12)
        title = node.get("title") or node["id"]
        if label == "Claim":
            title = f"[{props.get('status','?')}] {title}"
        nodes.append(
            Node(
                id=node["id"],
                label=_short_label(node.get("title") or node["id"]),
                size=size,
                color=colour,
                title=title,
            )
        )

    ids = {n["id"] for n in snapshot["nodes"]}
    edges = [
        Edge(source=e["source"], target=e["target"], label=e["label"])
        for e in snapshot["edges"]
        if e["source"] in ids and e["target"] in ids
    ]

    # Stronger repulsion and longer springs, because claim text makes for long
    # labels that otherwise pile up on top of each other.
    config = Config(
        width="100%",
        height=height,
        directed=True,
        hierarchical=False,
        nodeHighlightBehavior=True,
        highlightColor="#F7E7A1",
        collapsible=False,
        node={"labelProperty": "label"},
        link={"labelProperty": "label", "renderLabel": False, "title": "label"},
        physics={
            "enabled": True,
            "solver": "barnesHut",
            "barnesHut": {
                "gravitationalConstant": -20000,
                "centralGravity": 0.2,
                "springLength": 260,
                "springConstant": 0.04,
                "avoidOverlap": 0.5,
            },
            "stabilization": {"enabled": True, "iterations": 500, "fit": True},
        },
    )
    agraph(nodes=nodes, edges=edges, config=config)

    legend = " · ".join(
        f"<span style='color:{colour};font-weight:700'>■</span> {label}"
        for label, colour in NODE_COLOURS.items()
        if label in {"Agent", "Claim", "KernelFact", "Source", "Infection", "Antibody"}
    )
    st.markdown(
        f"<div style='font-size:0.85rem'>{legend} · "
        f"<span style='color:{QUARANTINED_COLOUR};font-weight:700'>■</span> quarantined · "
        f"<span style='color:{SUSPECT_COLOUR};font-weight:700'>■</span> support invalidated"
        "</div>",
        unsafe_allow_html=True,
    )


def render_dashboard(swarm: Swarm) -> None:
    infections = swarm.infections()
    active = [i for i in infections if i["status"] == "active"]
    contained = [i for i in infections if i["status"] != "active"]

    cols = st.columns(3)
    cols[0].metric("Active outbreaks", len(active))
    cols[1].metric("Contained", len(contained))
    worst = max((i["severity"] for i in infections), default=0.0)
    cols[2].metric("Peak severity", f"{worst:.1f}")

    st.markdown("##### Infections")
    if not infections:
        st.caption("No infection detected.")
    for infection in infections:
        icon = "🔴" if infection["status"] == "active" else "🟢"
        with st.expander(
            f"{icon} {infection['type']} · severity {infection['severity']:.1f} "
            f"· spread {infection['spread_count']}"
        ):
            st.markdown(f"**Root claim:** {infection['root_claim']}")
            st.caption(
                f"Detected {infection['detected_at']} · status {infection['status']}"
            )

    st.markdown("##### Trust, derived from the graph")
    trust = swarm.trust_table()
    if trust:
        st.dataframe(
            [
                {
                    "agent": row["agent"],
                    "base": round(row["base_trust"], 2),
                    "infections": row["active_infections"],
                    "effective": round(row["effective_trust"], 2),
                }
                for row in trust
            ],
            width="stretch",
            hide_index=True,
        )

    st.markdown("##### Immunity memory (Q8)")
    effectiveness = swarm.effectiveness()
    if effectiveness:
        st.dataframe(effectiveness, width="stretch", hide_index=True)
    else:
        st.caption("No antibodies have responded yet.")



def render_kernel(swarm: Swarm) -> None:
    """The kernel of truth: what the swarm has learned is *right*.

    Infection/Claim record what was wrong. This panel is the other half, the
    facts the antibody established, each with the source that backs it, and the
    claims currently in conflict with them.
    """
    st.markdown("##### Kernel of truth")
    stats = swarm.kernel_stats()
    cols = st.columns(3)
    cols[0].metric("Facts held", stats["facts"])
    cols[1].metric("Claims aligned", stats["aligned_edges"])
    cols[2].metric("In conflict", stats["conflict_edges"])

    facts = swarm.kernel_facts()
    if facts:
        st.dataframe(
            [
                {
                    "fact": fact["text"],
                    "source": fact["source"] or "-",
                    "trust": fact["trust_level"],
                }
                for fact in facts
            ],
            width="stretch",
            hide_index=True,
        )
        with st.expander("Provenance, who taught the swarm this?"):
            for fact in facts:
                prov = swarm.fact_provenance(fact["id"]) or {}
                st.markdown(f"**{fact['text']}**")
                st.caption(
                    f"established by `{prov.get('established_by') or '-'}` · "
                    f"source {prov.get('source') or '-'} · {prov.get('created_at', '')}"
                )
                if prov.get("source_url"):
                    st.caption(prov["source_url"])
    else:
        st.caption(
            "Nothing established yet. The antibody adds a fact when it can cite a source, "
            "an unsourced correction is a meta-statement, not a truth."
        )

    summary = swarm.sanitizer_summary()
    if summary["fast_path_hits"]:
        st.caption(
            f"🛡️ Fast path: {summary['fast_path_hits']} claim(s) recognised against the kernel, "
            f"average {summary['avg_ms']}ms, no model call."
        )

    st.markdown("##### Durability")
    st.caption(
        f"{swarm.log_count()} writes in the append-only log. FalkorDB Cloud's free tier has "
        "no persistence, so the graph is a projection, rebuild it and it comes back."
    )
    with st.expander("Write-ahead log (what a rebuild replays)"):
        rows = swarm.log_entries(limit=40)
        if not rows:
            st.caption("Nothing logged yet. Press **Rebuild from log** after running the mission.")
        else:
            st.dataframe(
                [{"seq": r["seq"], "event": r["label"], "statement": r["summary"]} for r in rows],
                width="stretch",
                hide_index=True,
            )
            st.caption("Most frequent: " + ", ".join(
                f"{label} ×{n}" for label, n in list(swarm.log_label_counts().items())[:5]
            ))


# ----------------------------------------------------------------------- page


def run_mission(swarm: Swarm, store: GraphStore, placeholder) -> None:
    # Always replay from a clean graph. The scripted cascade assumes no prior
    # claims, and a leftover claim would silently become the derivation parent
    # of the Analyst's line, the demo would then show the wrong cascade.
    #
    # reset_all() clears the log as well: resetting only the graph would leave
    # the log describing data that no longer exists, and the next rebuild would
    # replay both runs with colliding claim ids.
    swarm.reset_all()
    swarm.start(MARS_TOPIC)
    for persona, text, note in MARS_CASCADE:
        placeholder.markdown(f"**{persona.name}** speaking…")
        swarm.say(persona, text)
        time.sleep(0.35)
    placeholder.markdown("Scanning the graph for infection…")
    swarm.scanner.sweep()
    time.sleep(0.35)
    placeholder.markdown("Dispatching antibodies…")
    swarm.dispatch_antibodies()
    time.sleep(0.35)
    placeholder.markdown("Immune response complete.")


def reinject(swarm: Swarm) -> str:
    persona, text, _ = MARS_REPEAT
    recall = swarm.recall(text)
    if not recall.hit:
        return "No prior infection matched, nothing to recall."
    turn = swarm.say(persona, text, sweep=False)
    swarm.contain_repeat(turn.claim_ids[0], recall)

    # A first re-injection matches the paraphrase by similarity and carries a
    # score. Clicking again matches the now-quarantined identical claim by
    # fingerprint, which has no score, so this must not blindly format one.
    if recall.score is not None:
        how = f"{recall.method}, score {recall.score:.2f}"
    else:
        how = f"{recall.method}, exact match"
    return (
        f"**Immunity recall fired** ({how}). Claim quarantined on sight, "
        "no cascade re-detection was needed."
    )


PHASE_ICON = {
    "asserted": "🗣️",
    "sanitised": "🔎",
    "grounded": "🌐",
    "infected": "🦠",
    "spread": "↗️",
    "antibody": "🛡️",
    "arrested": "✅",
    "clear": "✅",
    "unknown": "❔",
    "rejected": "❔",
}


def _run_claim_stream(swarm: Swarm, text: str) -> dict | None:
    """Run a submitted claim, showing the cascade as the graph changes.

    Every line here corresponds to a real write: the agents genuinely derive
    from the claim, and the severity shown is read back from Q4 after each one
    rather than narrated. If the claim is not refuted, the run stops early and
    nothing is contaminated.
    """
    placeholder = st.empty()
    severity_slot = st.empty()
    lines: list[str] = []
    result: dict | None = None

    for step in swarm.submit_claim_stream(text):
        phase = step["phase"]
        lines.append(f"{PHASE_ICON.get(phase, '•')}  {step['message']}")
        placeholder.markdown("\n\n".join(lines))
        if step.get("severity"):
            severity_slot.metric("Infection severity", f"{step['severity']:.1f}")
        if phase in {"asserted", "infected", "spread", "arrested"}:
            time.sleep(0.55)
        if "result" in step:
            result = step["result"]

    if result and result.get("arrest"):
        severity_slot.metric("Infection severity", "contained")
    return result


COMPANY_ICON = {
    "seeded": "📚",
    "conflict": "⚖️",
    "spread": "↗️",
    "infected": "🦠",
    "arrested": "🛡️",
    "revised": "📝",
    "stale": "⏳",
    "done": "✅",
}


def _run_company_stream(swarm: Swarm, brain: CompanyBrain) -> None:
    """Play the company scenario, showing each step as it lands."""
    placeholder = st.empty()
    severity_slot = st.empty()
    lines: list[str] = []

    for step in brain.scenario():
        lines.append(f"{COMPANY_ICON.get(step['phase'], '•')}  {step['message']}")
        placeholder.markdown("\n\n".join(lines))
        if step.get("severity"):
            severity_slot.metric("Infection severity", f"{step['severity']:.1f}")
        if step["phase"] in {"conflict", "spread", "infected", "arrested", "revised", "stale"}:
            time.sleep(0.65)

    if lines:
        severity_slot.metric("Infection severity", "contained")


def render_audit(swarm: Swarm) -> None:
    """Track 01/02: measure what the defence catches AND what it wrongly catches."""
    st.markdown("#### Self-audit, two-sided, because one number is gameable")
    st.caption(
        "A defence that flags everything scores **100% recall** and is worthless. Recall alone "
        "cannot tell you that. So the audit generates adversarial probes from the swarm's own "
        "kernel and scores the detector both ways: precision against hand-written true "
        "restatements, autoimmunity against the host, and whether it fired at the **right fact**."
    )

    # Streamlit keeps session state across a code change but does not re-import
    # the modules under src/, so a Report cached before an upgrade can outlive the
    # class that produced it. Asking a stale object for an attribute added later
    # raises AttributeError on a page that otherwise works. Validate the cached
    # object and drop it rather than trusting it.
    before = st.session_state.get("audit_before")
    if before is not None and not all(
        hasattr(before, attr)
        for attr in ("precision", "recall", "f1", "autoimmunity", "attribution", "outcomes")
    ):
        st.session_state.pop("audit_before", None)
        st.session_state.pop("audit_after", None)
        st.session_state.pop("audit_vaccinated", None)
        st.warning(
            "The cached audit result came from an earlier version of this app, so it has been "
            "discarded. Run the audit again."
        )
        before = None

    auditor = KernelAuditor(swarm)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🔬  Run self-audit", type="primary", width="stretch"):
            st.session_state["audit_before"] = auditor.audit()
            st.session_state.pop("audit_after", None)
    with col_b:
        if st.button("💉  Vaccinate blind spots", width="stretch",
                     disabled="audit_before" not in st.session_state):
            created = auditor.vaccinate(st.session_state["audit_before"])
            st.session_state["audit_vaccinated"] = len(created)
            st.session_state["audit_after"] = auditor.audit()

    facts = swarm.kernel.facts()
    if len(facts) < 3:
        st.warning(
            f"The kernel holds {len(facts)} fact(s), too few to audit meaningfully. "
            "The audit mutates facts, so it needs a corpus. Load the company documents, "
            "which come with numbers, policies and a shared subject."
        )
        if st.button("📚  Load company knowledge, then audit", type="primary"):
            CompanyBrain(swarm).seed()
            st.rerun()
        return

    if before is None:
        st.info(
            "Nothing audited yet. The audit mutates the kernel's own facts and tests the "
            "defence against them."
        )
        return

    after = st.session_state.get("audit_after")
    shown = after or before
    cols = st.columns(5)
    cols[0].metric("Precision", f"{shown.precision:.0%}",
                   help="Of every flag raised, how many were genuinely false")
    cols[1].metric("Recall", f"{shown.recall:.0%}",
                   help="Of every generated falsehood, how many were caught (also called coverage)")
    cols[2].metric("F1", f"{shown.f1:.2f}",
                   help="Harmonic mean of precision and recall")
    cols[3].metric("Autoimmunity", f"{shown.autoimmunity:.0%}",
                   help="True statements wrongly flagged. The immune system attacking the host.")
    cols[4].metric("Attribution", f"{shown.attribution:.0%}",
                   help="Caught against the RIGHT fact, not by luck")

    st.caption(
        f"{shown.probes} probes: {shown.contradictions} contradictions, {shown.controls} controls "
        f"that must not fire, in {shown.elapsed_ms:.0f} ms."
    )

    if after is not None:
        st.success(
            f"Vaccinated {st.session_state.get('audit_vaccinated', 0)} blind spots. "
            f"Uncovered {len(before.uncovered)} → {len(after.uncovered)}, "
            f"autoimmunity {before.autoimmunity:.0%} → {after.autoimmunity:.0%}."
        )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("##### Uncovered, falsehoods no fact contradicts")
        if shown.uncovered:
            st.dataframe(
                [{"statement": u["text"]} for u in shown.uncovered],
                width="stretch", hide_index=True,
            )
            st.caption(
                "Nothing here is contradicted by the kernel, so no comparison can catch it. "
                "What is missing is the knowledge that the thing does not exist, which is "
                "why vaccinating means recording it as known-false."
            )
        else:
            st.caption("None. Every generated mutation is either caught or recorded.")
    with right:
        st.markdown("##### Every probe, and what happened")
        st.dataframe(
            [
                {"probe": o["kind"], "expected": o["expected"], "got": o["result"],
                 "statement": o["text"][:58]}
                for o in shown.outcomes
            ],
            width="stretch", hide_index=True, height=260,
        )
        st.caption(
            f"{shown.caught} of {shown.contradictions} contradictions caught; "
            f"{len(shown.misattributed)} caught against the wrong fact."
        )
        if shown.false_alarms:
            st.error(f"{shown.false_alarms} true statements were flagged. That is autoimmunity.")


def render_company(swarm: Swarm) -> None:
    """Track 03: a shared brain over one company's documents."""
    st.markdown(f"#### {COMPANY}, a company brain that can be corrected")
    st.caption(
        "Every department agent answers from the same documents. The failure this prevents is "
        "not retrieval failure: it is one agent's confident wrong **price** being repeated by "
        "another department until it becomes doctrine. The corpus is synthetic, so no private "
        "information is anywhere near it."
    )

    brain = CompanyBrain(swarm)
    col_a, col_b = st.columns([1, 1], gap="medium")
    with col_a:
        if st.button("▶  Ask the company brain", type="primary", width="stretch"):
            _run_company_stream(swarm, brain)
    with col_b:
        st.metric("Documents in the brain", len(DOCUMENTS))

    st.divider()
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown("##### Documents")
        st.dataframe(
            [
                {"document": doc.title, "department": doc.department, "facts": len(doc.facts)}
                for doc in DOCUMENTS
            ],
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Each document is a `Source` node; each fact it establishes is a `KernelFact` "
            "linked to it, so provenance is a traversal rather than a citation a model wrote "
            "from memory."
        )
    with right:
        st.markdown("##### Facts in force")
        facts = swarm.kernel_facts()
        if facts:
            st.dataframe(
                [
                    {"fact": f["text"], "trust": f["trust_level"], "source": f["source"] or "-"}
                    for f in facts
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("Nothing loaded yet, run the scenario.")
        st.markdown("##### Policy drift")
        st.caption(
            "When a price changes the old fact is **superseded**, not deleted. Claims that were "
            "correct last week are re-judged against the new truth, so nothing stale stays "
            "blessed. A brain that cannot forget is as dangerous as one that cannot learn."
        )


def render_check_claim(swarm: Swarm) -> None:
    """Let a visitor submit a claim the swarm has never seen."""
    st.markdown("#### Submit a claim the swarm has never seen")
    st.caption(
        "Grounded against **Wikipedia**, free, public, and citable, then run through the same "
        "immune machinery as the Mars scenario. If a public source refutes it, the claim is "
        "quarantined, an antibody cites that source, and the fact enters the kernel."
    )

    examples = [
        "Venus has a breathable atmosphere",
        "The Earth orbits the Sun",
        "Mars is made of cheese",
    ]
    cols = st.columns(len(examples))
    for column, example in zip(cols, examples):
        if column.button(example, key=f"ex_{example[:12]}", width="stretch"):
            st.session_state["claim_input"] = example

    # A plain input plus a button rather than a form: forms swallow the Enter key
    # in some harnesses and make the flow harder to exercise end to end.
    text = st.text_input(
        "Claim",
        key="claim_input",
        placeholder="e.g. Venus has a breathable atmosphere",
        label_visibility="collapsed",
    )
    submitted = st.button("⚖️  Check this claim", type="primary")

    if submitted and text.strip():
        st.session_state["last_check"] = _run_claim_stream(swarm, text.strip())

    result = st.session_state.get("last_check")
    if not result:
        st.info("Nothing checked yet. Try one of the examples above, or write your own claim.")
        return

    if not result["accepted"]:
        st.warning(result["reason"])
        return

    evidence = result["evidence"]
    st.divider()
    st.markdown(f"**Claim:** {result['claim_text']}")

    if result.get("ground_error"):
        st.warning(f"Grounding unavailable: {result['ground_error']}")
    elif evidence is None:
        st.warning("Grounding unavailable.")
    elif evidence.is_false:
        st.error(f"**Refuted** by {evidence.title}, the claim is false.")
    elif evidence.verdict == "supports":
        st.success(f"**Corroborated** by {evidence.title}.")
    else:
        st.info(f"**Inconclusive**, a public source was found but does not settle it.")

    if evidence is not None:
        with st.container(border=True):
            if evidence.sentence:
                st.markdown(f"> {evidence.sentence.strip()[:600]}")
            if evidence.url:
                st.markdown(f"Source: [{evidence.title}]({evidence.url})")
            st.caption(f"Matched by lexical grounding, {evidence.reason}")

    if result["arrest"]:
        arrest = result["arrest"]
        st.markdown("##### 🛡️ Arrested by the same mechanism")
        cols = st.columns(3)
        cols[0].metric("Infection", "contained")
        cols[1].metric("Antibody", "external citation")
        cols[2].metric("Kernel fact", "added" if arrest["fact_id"] else "none")
        st.caption(
            f"Antibody `{arrest['antibody_id']}` cites **{arrest['source']}**; the claim is "
            f"quarantined and the fact is now in the kernel, so a repeat is caught without "
            f"touching the network."
        )
    elif evidence is not None and not evidence.is_false:
        st.caption("Nothing to arrest, the swarm only quarantines what a source refutes.")

    st.divider()
    st.markdown("##### What the swarm already knew")
    verdicts = result.get("verdicts") or []
    if verdicts:
        st.caption(
            "Sanitised against the kernel before any network call: "
            + ", ".join(f"{v['verdict']} ({v['ms']}ms)" for v in verdicts)
        )


def render_knowledge(swarm: Swarm) -> None:
    """Kernel of truth and durability, gathered in one place."""
    left, right = st.columns([1.15, 1], gap="large")
    with left:
        st.markdown("#### Kernel of truth")
        st.caption(
            "What the swarm holds as **true**, as opposed to Infection/Claim which record what "
            "was wrong. Written by antibodies, consulted by the sanitiser."
        )
        render_kernel(swarm)
    with right:
        st.markdown("#### Durability")
        st.caption(
            "FalkorDB Cloud's free tier has no persistence. Every write is appended to a log "
            "first, so the graph is a projection that can be rebuilt."
        )
        st.metric("Writes in the log", swarm.log_count())
        if st.button("♻️  Rebuild from log", width="stretch", key="rebuild_inline"):
            st.session_state["rebuild_note"] = _rebuild(swarm)
            st.rerun()
        rows = swarm.log_entries(limit=12)
        if rows:
            st.dataframe(
                [{"#": r["seq"], "event": r["label"], "statement": r["summary"]} for r in rows],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("Nothing logged yet.")


def _rebuild(swarm: Swarm) -> str:
    result = swarm.rebuild_from_log()
    if result.get("error"):
        return result["error"]
    if result["identical"]:
        return (
            f"Rebuilt {result['applied']} writes, the graph is **identical** to the one "
            f"before the wipe ({result['nodes']} nodes)."
        )
    return f"Rebuilt {result['applied']} writes, but the result differs ({result['failed']} failed)."


def main() -> None:
    swarm, store, log, settings = get_context()

    st.title("🧬 ImmuneGraph")
    st.markdown(
        "**A self-healing memory for AI agent swarms.** Ordinary memory records what agents "
        "*said*. ImmuneGraph records **what supports what**, so an unsupported claim, a "
        "self-justifying cycle and a cascade all become graph traversals, and the swarm "
        "arrests them."
    )

    if not require_db(store):
        return

    store.create_indexes()
    notice = restore_if_empty(swarm, store, settings)
    if notice:
        st.info(notice)

    with st.sidebar:
        st.header("Scenario")
        st.markdown(
            "Three agents plan a Mars mission. The first asserts something with no source. "
            "The other two reason correctly from it."
        )
        if st.button("▶  Run Mars mission", type="primary", width="stretch"):
            placeholder = st.empty()
            run_mission(swarm, store, placeholder)
            st.rerun()
        if st.button("💉  Re-inject the false claim", width="stretch"):
            st.session_state["recall_note"] = reinject(swarm)
            st.rerun()
        if st.button("🗑  Reset graph", width="stretch"):
            swarm.reset_all()
            st.session_state.pop("recall_note", None)
            st.session_state.pop("rebuild_note", None)
            st.session_state.pop("last_check", None)
            st.rerun()
        if st.button("♻️  Rebuild from log", width="stretch"):
            st.session_state["rebuild_note"] = _rebuild(swarm)
            st.rerun()

        st.divider()
        st.caption(
            f"Connected to `{store.settings.host}:{store.settings.port}` · graph "
            f"`{store.settings.graph}` · extractor `{store.settings.extractor}` · "
            f"secrets bridged {STREAMLIT_SECRETS_BRIDGED}"
        )
        with st.expander("Graph data model"):
            st.markdown(
                "**Nodes**, `Agent` `Claim` `KernelFact` `Source` `Infection` `Antibody` "
                "`Message` `Session`\n\n"
                "**Memory**, episodic (`Message`/`Session`), semantic "
                "(`Claim`/`Source`/`KernelFact`), procedural (`Antibody`/`Infection`)\n\n"
                "**Edges**, `ASSERTS` `SUPPORTED_BY` `DERIVED_FROM` `CONTRADICTS` `REPEATS` "
                "`TARGETS` `SPREAD_TO` `NEUTRALIZES` `ESTABLISHES` `CONFLICTS_WITH`\n\n"
                "`DERIVED_FROM` points **child → parent**, so cascades walk "
                "`(downstream)-[:DERIVED_FROM*1..5]->(root)`."
            )
        with st.expander("The queries doing the work"):
            st.markdown(
                "**Detect new lies:** **Q1** cycles · **Q2** unsupported roots · **Q3** "
                "contradictions · **Q4** cascade reach (severity) · **Q5** derived trust · "
                "**Q6/Q7** immunity recall · **Q8** effectiveness\n\n"
                "**Recognise known ones:** **K1** kernel facts · **K2** conflicts · **K3** "
                "kernel health · **K4** unsanitised claims · **K5** provenance"
            )
            st.caption("Full text in docs/GRAPH_MODEL.md and src/immunegraph/queries.py")
        with st.expander("Verify it yourself"):
            st.code(
                "docker compose up -d\n"
                "python -m immunegraph.verify_queries\n"
                "python -m pytest\n"
                "python -m immunegraph.demo",
                language="bash",
            )
            st.caption("Every query is executed against a live instance before commit.")

    if note := st.session_state.get("rebuild_note"):
        st.success(note)
    if note := st.session_state.get("recall_note"):
        st.success(note)

    demo_tab, claim_tab, company_tab, audit_tab, knowledge_tab = st.tabs(
        ["  Live demo  ", "  Check any claim  ", "  Company brain  ", "  Self-audit  ",
         "  Kernel & durability  "]
    )

    with demo_tab:
        left, middle, right = st.columns([1.05, 1.5, 1.05], gap="medium")
        with left:
            st.subheader("Conversation")
            render_transcript(swarm)
        with middle:
            st.subheader("Belief graph")
            render_graph(swarm)
        with right:
            st.subheader("Immune dashboard")
            render_dashboard(swarm)

    with claim_tab:
        render_check_claim(swarm)

    with company_tab:
        render_company(swarm)

    with audit_tab:
        render_audit(swarm)

    with knowledge_tab:
        render_knowledge(swarm)

    st.divider()
    st.caption(
        "Track 01, Agents That Act on Connected Data · "
        "Track 02, Agent Memory and Coordination · "
        "Track 03, Company Brain · "
        "Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB)"
    )


if __name__ == "__main__":
    main()
