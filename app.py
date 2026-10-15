"""ImmuneGraph — Streamlit UI.

Three columns: the conversation, the live belief graph, and the immune
dashboard. Every panel is driven by a Cypher query; nothing is computed in
Python that the graph should be computing.

    streamlit run app.py
"""

from __future__ import annotations

import time

import streamlit as st

st.set_page_config(
    page_title="ImmuneGraph — self-healing memory for agent swarms",
    page_icon="🧬",
    layout="wide",
)

from immunegraph.agents import MARS_CASCADE, MARS_REPEAT, MARS_TOPIC  # noqa: E402
from immunegraph.config import Settings  # noqa: E402
from immunegraph.db import GraphStore  # noqa: E402
from immunegraph.swarm import Swarm  # noqa: E402

# --------------------------------------------------------------------- palette

NODE_COLOURS = {
    "Agent": "#4C8BF5",
    "Claim": "#2E9E5B",
    "Source": "#9AA0A6",
    "Infection": "#E8453C",
    "Antibody": "#F2B705",
    "Message": "#C9D3E0",
    "Session": "#DCE3EC",
    "Tenant": "#DCE3EC",
}

QUARANTINED_COLOUR = "#7A2E28"


# ------------------------------------------------------------------- resources


@st.cache_resource(show_spinner=False)
def get_context() -> tuple[Swarm, GraphStore]:
    store = GraphStore(Settings.from_env())
    store.create_indexes()
    swarm = Swarm(store)
    return swarm, store


def require_db(store: GraphStore) -> bool:
    if store.ping():
        return True
    st.error(
        f"Cannot reach FalkorDB at **{store.settings.host}:{store.settings.port}**.\n\n"
        "Start it with `docker compose up -d`, or point `FALKORDB_HOST` in `.env` at a "
        "FalkorDB Cloud instance."
    )
    return False


# ------------------------------------------------------------------ rendering


def render_transcript(swarm: Swarm) -> None:
    rows = swarm.store.rows(
        """
        MATCH (a:Agent)-[:SPOKE]->(m:Message)
        OPTIONAL MATCH (m)-[:MENTIONS]->(c:Claim)
        RETURN a.name, m.text, m.seq, collect(c.status)
        ORDER BY m.seq
        """
    )
    if not rows:
        st.caption("No conversation yet. Run the Mars mission to begin.")
        return
    for name, text, seq, statuses in rows:
        badge = ""
        if "quarantined" in (statuses or []):
            badge = "  🚫 *quarantined*"
        elif statuses and statuses[0]:
            badge = "  ✅ *believed*"
        with st.chat_message("assistant" if name != "Antibody System" else "system"):
            st.markdown(f"**{name}**{badge}")
            st.markdown(text)


def _short_label(text: str, words: int = 5, chars: int = 30) -> str:
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
        st.dataframe(snapshot["nodes"], use_container_width=True)
        return

    nodes = []
    for node in snapshot["nodes"]:
        label = node["label"]
        props = node.get("properties") or {}
        colour = NODE_COLOURS.get(label, "#CCCCCC")
        size = 18
        if label == "Claim" and props.get("status") == "quarantined":
            colour = QUARANTINED_COLOUR
            size = 22
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
        link={"labelProperty": "label", "renderLabel": True},
        physics={
            "enabled": True,
            "solver": "barnesHut",
            "barnesHut": {
                "gravitationalConstant": -16000,
                "centralGravity": 0.2,
                "springLength": 220,
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
        if label in {"Agent", "Claim", "Source", "Infection", "Antibody"}
    )
    st.markdown(
        f"<div style='font-size:0.85rem'>{legend} · "
        f"<span style='color:{QUARANTINED_COLOUR};font-weight:700'>■</span> quarantined claim"
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
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("##### Immunity memory (Q8)")
    effectiveness = swarm.effectiveness()
    if effectiveness:
        st.dataframe(effectiveness, use_container_width=True, hide_index=True)
    else:
        st.caption("No antibodies have responded yet.")


# ----------------------------------------------------------------------- page


def run_mission(swarm: Swarm, store: GraphStore, placeholder) -> None:
    # Always replay from a clean graph. The scripted cascade assumes no prior
    # claims, and a leftover claim would silently become the derivation parent
    # of the Analyst's line — the demo would then show the wrong cascade.
    store.reset()
    store.create_indexes()
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
        return "No prior infection matched — nothing to recall."
    turn = swarm.say(persona, text, sweep=False)
    swarm.contain_repeat(turn.claim_ids[0], recall)

    # A first re-injection matches the paraphrase by similarity and carries a
    # score. Clicking again matches the now-quarantined identical claim by
    # fingerprint, which has no score — so this must not blindly format one.
    if recall.score is not None:
        how = f"{recall.method}, score {recall.score:.2f}"
    else:
        how = f"{recall.method}, exact match"
    return (
        f"**Immunity recall fired** ({how}). Claim quarantined on sight — "
        "no cascade re-detection was needed."
    )


def main() -> None:
    swarm, store = get_context()

    st.title("🧬 ImmuneGraph")
    st.markdown(
        "**A self-healing memory for AI agent swarms.** Multi-agent systems fail in a way "
        "ordinary memory layers cannot see: one agent invents a claim, another derives a plan "
        "from it, and the swarm acts on a lie. ImmuneGraph models belief as a graph, so "
        "unsupported claims, self-justifying cycles and cascades become *traversals* — and "
        "FalkorDB is the immune system, not a picture of one."
    )
    st.caption(f"Connected to {store.settings.host}:{store.settings.port} · graph "
               f"`{store.settings.graph}` · extractor `{store.settings.extractor}`")

    if not require_db(store):
        return

    with st.sidebar:
        st.header("Scenario")
        st.markdown(
            "Three agents plan a Mars mission. The first asserts something with no source. "
            "The other two reason correctly from it."
        )
        if st.button("▶  Run Mars mission", type="primary", use_container_width=True):
            placeholder = st.empty()
            run_mission(swarm, store, placeholder)
            st.rerun()
        if st.button("💉  Re-inject the false claim", use_container_width=True):
            st.session_state["recall_note"] = reinject(swarm)
            st.rerun()
        if st.button("🗑  Reset graph", use_container_width=True):
            store.reset()
            store.create_indexes()
            st.session_state.pop("recall_note", None)
            st.rerun()

        st.divider()
        with st.expander("Graph data model"):
            st.markdown(
                "**Nodes** — `Agent` `Claim` `Source` `Infection` `Antibody` `Message` `Session`\n\n"
                "**Memory** — episodic (`Message`/`Session`), semantic (`Claim`/`Source`/`Agent`), "
                "procedural (`Antibody`/`Infection`)\n\n"
                "**Edges** — `ASSERTS` `SUPPORTED_BY` `DERIVED_FROM` `CONTRADICTS` `REPEATS` "
                "`TRUSTS` `TARGETS` `SPREAD_TO` `NEUTRALIZES` `INJECTS`\n\n"
                "`DERIVED_FROM` points **child → parent**, so cascades walk "
                "`(downstream)-[:DERIVED_FROM*1..5]->(root)`."
            )
        with st.expander("The queries doing the work"):
            st.markdown(
                "**Q1** cycles in the support structure\n\n"
                "**Q2** active claims with no trusted source and no parent\n\n"
                "**Q3** contradiction clusters between agents\n\n"
                "**Q4** cascade reach — severity is *defined* as this traversal\n\n"
                "**Q5** trust derived from exposure\n\n"
                "**Q6/Q7** immunity recall, exact and paraphrase\n\n"
                "**Q8** antibody effectiveness"
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

    if note := st.session_state.get("recall_note"):
        st.success(note)

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

    st.divider()
    st.caption(
        "Track 01 — Agents That Act on Connected Data · "
        "Track 02 — Agent Memory and Coordination · "
        "Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB)"
    )


if __name__ == "__main__":
    main()
