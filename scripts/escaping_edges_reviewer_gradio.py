import sys
import os
import json
import tempfile
import gradio as gr
import pm4py
from pm4py.objects.conversion.log import converter as log_converter
from pm4py.objects.conversion.process_tree import converter as pt_converter
from pm4py.objects.log.obj import EventLog, Trace, Event
from pm4py.util import constants as pm4py_constants
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import custom_precision_variants_align_etconformance as custom_ae
import save_proposals

VSEP = pm4py_constants.DEFAULT_VARIANT_SEP
ACTIVITY_KEY = "concept:name"


# --- helpers ---

def build_prefix_rows(result):
    rows = []
    for prefix, data in result["prefixes"].items():
        if data.get("unfit", False) or data["n_ee"] == 0:
            continue
        rows.append({
            "Prefix": " \u2192 ".join(prefix.split(VSEP)),
            "Support": data["count"],
            "|EE|": data["n_ee"],
            "Impact": data["count"] * data["n_ee"],
            "Escaping Edges": ", ".join(sorted(data["escaping_edges"])),
        })
    rows.sort(key=lambda r: -r["Impact"])
    return rows


def build_state_rows(result):
    groups = defaultdict(lambda: {"support": 0, "prefixes": []})
    for prefix, data in result["prefixes"].items():
        if data.get("unfit", False):
            continue
        state = frozenset(data["activated_transitions"])
        for ee in data["escaping_edges"]:
            key = (state, ee)
            groups[key]["support"] += data["count"]
            groups[key]["prefixes"].append(prefix)

    rows = []
    for (state, ee), info in sorted(groups.items(), key=lambda x: -x[1]["support"]):
        example = " \u2192 ".join(min(info["prefixes"], key=len).split(VSEP))
        rows.append({
            "Example Prefix": example,
            "EE": ee,
            "Support": info["support"],
            "State (Activated Transitions)": ", ".join(sorted(state)),
        })
    return rows


def discover(log_path, progress=gr.Progress()):
    if not log_path or not log_path.endswith(".xes"):
        raise gr.Error("Please select a .xes file.")

    progress(0.1, "Loading log...")
    event_log = log_converter.apply(pm4py.read_xes(log_path))

    progress(0.3, "Discovering process tree...")
    process_tree = pm4py.discover_process_tree_inductive(event_log)

    progress(0.5, "Converting to Petri net...")
    net, im, fm = pt_converter.apply(process_tree)

    progress(0.6, "Computing metrics...")
    log_fitness = pm4py.fitness_alignments(event_log, net, im, fm, multi_processing=False)["log_fitness"]
    precision = pm4py.precision_alignments(event_log, net, im, fm, multi_processing=False)
    denominator = log_fitness + precision
    f1 = 2 * (log_fitness * precision) / denominator if denominator != 0 else 0.0
    size = len(net.places) + len(net.transitions) + len(net.arcs)

    progress(0.8, "Computing escaping edges...")
    result = custom_ae.apply(event_log, net, im, fm)

    progress(0.9, "Building proposals...")
    proposals = save_proposals.build_proposals_data(event_log, result, VSEP, activity_key=ACTIVITY_KEY)

    # Save petri net visualization
    img_path = os.path.join(tempfile.gettempdir(), "petri_net_preview.png")
    pm4py.save_vis_petri_net(net, im, fm, img_path)

    # Build tables
    import pandas as pd
    prefix_rows = build_prefix_rows(result)
    state_rows = build_state_rows(result)
    prefix_df = pd.DataFrame(prefix_rows) if prefix_rows else pd.DataFrame(columns=["Prefix", "Support", "|EE|", "Impact", "Escaping Edges"])
    state_df = pd.DataFrame(state_rows) if state_rows else pd.DataFrame(columns=["Example Prefix", "EE", "Support", "State (Activated Transitions)"])

    state = {
        "log": event_log,
        "net": net,
        "im": im,
        "fm": fm,
        "result": result,
        "proposals": proposals,
        "desirable": [],
        "undesirable": [],
        "idx": 0,
    }

    metrics = (
        f"Fitness: {log_fitness:.4f}\n"
        f"Precision: {precision:.4f}\n"
        f"F1 Score: {f1:.4f}\n"
        f"Model Size: {size}\n"
        f"Sum Escaping Edges: {result['sum_ee']}"
    )

    prefix_str, ee_str, trace_str, nav_txt, des_str, undes_str = _proposal_display(proposals, 0, [], [])
    return (
        img_path,
        metrics,
        prefix_df,
        state_df,
        state,
        prefix_str,
        ee_str,
        trace_str,
        nav_txt,
        des_str,
        undes_str,
    )


def _proposal_display(proposals, idx, desirable, undesirable):
    if not proposals or idx >= len(proposals):
        return (
            "No proposals",
            "No proposals",
            "",
            f"0 / 0",
            "(empty)",
            "(empty)",
        )
    p = proposals[idx]
    full = p["prefix_acts"] + [p["ee"]] + p.get("suggestion", [])
    prefix_str = " \u2192 ".join(p["prefix_acts"])
    ee_str = p["ee"]
    trace_str = " \u2192 ".join(full)

    des_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(desirable)) if desirable else "(empty)"
    undes_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(undesirable)) if undesirable else "(empty)"

    return (
        prefix_str,
        ee_str,
        trace_str,
        f"{idx + 1} / {len(proposals)}   |   Desirable: {len(desirable)}   Undesirable: {len(undesirable)}",
        des_str,
        undes_str,
    )


def nav_next(state):
    if state is None or not state["proposals"]:
        return state, *[""] * 6
    idx = min(state["idx"] + 1, len(state["proposals"]) - 1)
    state["idx"] = idx
    return state, *_proposal_display(state["proposals"], idx, state["desirable"], state["undesirable"])


def nav_prev(state):
    if state is None or not state["proposals"]:
        return state, *[""] * 6
    idx = max(state["idx"] - 1, 0)
    state["idx"] = idx
    return state, *_proposal_display(state["proposals"], idx, state["desirable"], state["undesirable"])


def add_desirable(trace_text, state):
    if state is None:
        return state, *[""] * 6
    t = trace_text.strip()
    if not t:
        return state, *_proposal_display(state["proposals"], state["idx"], state["desirable"], state["undesirable"])
    p = state["proposals"][state["idx"]]
    if t not in state["desirable"]:
        state["desirable"].append(t)
    idx2 = min(state["idx"] + 1, len(state["proposals"]) - 1)
    state["idx"] = idx2
    return state, *_proposal_display(state["proposals"], idx2, state["desirable"], state["undesirable"])


def add_undesirable(trace_text, state):
    if state is None:
        return state, *[""] * 6
    t = trace_text.strip()
    if not t:
        return state, *_proposal_display(state["proposals"], state["idx"], state["desirable"], state["undesirable"])
    p = state["proposals"][state["idx"]]
    if t not in state["undesirable"]:
        state["undesirable"].append(t)
    idx2 = min(state["idx"] + 1, len(state["proposals"]) - 1)
    state["idx"] = idx2
    return state, *_proposal_display(state["proposals"], idx2, state["desirable"], state["undesirable"])


def export_desirable(state):
    if state is None or not state["desirable"]:
        raise gr.Error("No desirable traces to export.")
    out = EventLog()
    for s in state["desirable"]:
        acts = [a.strip() for a in s.split("\u2192") if a.strip()]
        trace = Trace()
        for a in acts:
            ev = Event()
            ev[ACTIVITY_KEY] = a
            trace.append(ev)
        out.append(trace)
    path = os.path.join(tempfile.gettempdir(), "desirable_log.xes")
    pm4py.write_xes(out, path)
    return path


def export_undesirable(state):
    if state is None or not state["undesirable"]:
        raise gr.Error("No undesirable traces to export.")
    out = EventLog()
    for s in state["undesirable"]:
        acts = [a.strip() for a in s.split("\u2192") if a.strip()]
        trace = Trace()
        for a in acts:
            ev = Event()
            ev[ACTIVITY_KEY] = a
            trace.append(ev)
        out.append(trace)
    path = os.path.join(tempfile.gettempdir(), "undesirable_log.xes")
    pm4py.write_xes(out, path)
    return path


# --- Build UI ---

with gr.Blocks(title="Escaping Edges Reviewer") as demo:
    state = gr.State()
    gr.Markdown("# Escaping Edges Reviewer")
    gr.Markdown("Discover a process model, analyze escaping edges, review proposals, and build desirable/undesirable logs.")

    with gr.Tabs():
        # ---- Tab 1: Load & Discover ----
        with gr.Tab("Load & Discover"):
            with gr.Row():
                log_input = gr.File(label="Event Log (.xes)", file_types=[".xes"], type="filepath")
            with gr.Row():
                discover_btn = gr.Button("Discover Model", variant="primary", size="lg")
            with gr.Row():
                metrics_box = gr.Textbox(label="Metrics", lines=6)
            with gr.Row():
                petri_img = gr.Image(label="Discovered Petri Net", height=400)
            with gr.Row():
                view_btn = gr.Button("Open Petri Net in Browser")
                view_html = gr.HTML(visible=False)

        # ---- Tab 2: Escaping Edges ----
        with gr.Tab("Escaping Edges"):
            gr.Markdown("### Prefix-level Escaping Edges (sorted by Impact)")
            prefix_table = gr.Dataframe(
                headers=["Prefix", "Support", "|EE|", "Impact", "Escaping Edges"],
                column_count=5,
                interactive=False,
                wrap=True,
            )
            gr.Markdown("### State-grouped Escaping Edges (sorted by Support)")
            state_table = gr.Dataframe(
                headers=["Example Prefix", "EE", "Support", "State (Activated Transitions)"],
                column_count=4,
                interactive=False,
                wrap=True,
            )

        # ---- Tab 3: Proposals Review ----
        with gr.Tab("Proposals Review"):
            with gr.Row():
                prefix_display = gr.Textbox(label="Prefix", lines=1, interactive=False)
            with gr.Row():
                ee_display = gr.Textbox(label="Escaping Edge", lines=1, interactive=False)
            with gr.Row():
                trace_input = gr.Textbox(label="Suggested Full Trace (editable)", lines=4)
            with gr.Row():
                nav_info = gr.Textbox(label="Progress", lines=1, interactive=False)
            with gr.Row():
                add_des_btn = gr.Button("Add to Desirable Log", variant="primary", size="lg", scale=1)
                add_undes_btn = gr.Button("Add to Undesirable Log", elem_id="undes-btn", variant="primary", size="lg", scale=1)
            gr.Markdown("---")
            with gr.Row():
                prev_btn = gr.Button("< Prev")
                next_btn = gr.Button("Next >")
            gr.Markdown("---")
            with gr.Row():
                with gr.Column():
                    gr.Markdown("### Desirable Traces")
                    des_list = gr.Textbox(label="", lines=6, interactive=False)
                with gr.Column():
                    gr.Markdown("### Undesirable Traces")
                    undes_list = gr.Textbox(label="", lines=6, interactive=False)
            gr.Markdown("---")
            with gr.Row():
                export_des_btn = gr.Button("Export Desirable Log (.xes)", variant="secondary")
                export_undes_btn = gr.Button("Export Undesirable Log (.xes)", variant="secondary")
            with gr.Row():
                export_des_file = gr.File(label="Download Desirable Log", visible=True)
                export_undes_file = gr.File(label="Download Undesirable Log", visible=True)

    # --- event wiring ---

    discover_outputs = [
        petri_img, metrics_box,
        prefix_table, state_table,
        state,
        prefix_display, ee_display, trace_input, nav_info,
        des_list, undes_list,
    ]

    view_btn.click(
        fn=lambda s: (
            pm4py.view_petri_net(s["net"], s["im"], s["fm"]) if s and s.get("net") else None,
            gr.HTML(visible=False),
        ),
        inputs=[state],
        outputs=[view_html],
    )

    discover_btn.click(
        fn=discover,
        inputs=[log_input],
        outputs=discover_outputs,
    )

    nav_outputs = [state, prefix_display, ee_display, trace_input, nav_info, des_list, undes_list]

    prev_btn.click(fn=nav_prev, inputs=[state], outputs=nav_outputs)
    next_btn.click(fn=nav_next, inputs=[state], outputs=nav_outputs)

    add_des_btn.click(
        fn=add_desirable,
        inputs=[trace_input, state],
        outputs=nav_outputs,
    )

    add_undes_btn.click(
        fn=add_undesirable,
        inputs=[trace_input, state],
        outputs=nav_outputs,
    )

    export_des_btn.click(
        fn=export_desirable,
        inputs=[state],
        outputs=[export_des_file],
    )

    export_undes_btn.click(
        fn=export_undesirable,
        inputs=[state],
        outputs=[export_undes_file],
    )


if __name__ == "__main__":
    demo.launch(
        share=False,
        theme=gr.themes.Soft(),
        css="#undes-btn { --button-primary-background-fill: #d32f2f; --button-primary-background-fill-hover: #b71c1c; --button-primary-text-color: white; } #undes-btn:hover { --button-primary-background-fill: #b71c1c; }",
    )
