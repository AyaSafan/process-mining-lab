import sys
import os
import json
import tempfile
import gradio as gr
import pm4py
from pm4py.objects.conversion.log import converter as log_converter
from pm4py.objects.log.obj import EventLog, Trace, Event
from pm4py.util import constants as pm4py_constants
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "InductiveMiner_bi"))
import custom_precision_variants_align_etconformance as custom_ae
import save_proposals

# Defer IMbi import to avoid startup failures if InductiveMiner_bi deps are missing
_imbi_available = False
try:
    from local_pm4py.algo.discovery.inductive import algorithm as _inductive_miner
    _imbi_available = True
except ImportError:
    pass
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


def discover(log_path, noise_threshold, progress=gr.Progress()):
    if not log_path or not log_path.endswith(".xes"):
        raise gr.Error("Please select a .xes file.")

    progress(0.1, "Loading log...")
    event_log = log_converter.apply(pm4py.read_xes(log_path))

    if not _imbi_available:
        raise gr.Error("Inductive Miner - bi is not available. Check the InductiveMiner_bi dependency.")

    progress(0.3, f"Discovering with IMbi (sup={noise_threshold})...")
    empty_log = EventLog()
    net, im, fm = _inductive_miner.apply_bi(
        event_log, empty_log,
        variant=_inductive_miner.Variants.IMbi,
        sup=noise_threshold,
        ratio=0,
        size_par=0,
    )

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

    dv = _proposal_display(proposals, 0, [], [])
    return (img_path, metrics, prefix_df, state_df, state, *dv)


def _proposal_display(proposals, idx, desirable, undesirable):
    des_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(desirable)) if desirable else "(empty)"
    undes_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(undesirable)) if undesirable else "(empty)"

    finished = not proposals or idx >= len(proposals)
    add_des_btn = gr.Button(interactive=not finished)
    add_undes_btn = gr.Button(elem_id="undes-btn", variant="primary", interactive=not finished)

    if finished:
        return (
            "* Done - all proposals reviewed *",
            "",
            "",
            f"0 / 0   |   Desirable: {len(desirable)}   Undesirable: {len(undesirable)}",
            des_str,
            undes_str,
            add_des_btn,
            add_undes_btn,
        )
    p = proposals[idx]
    full = p["prefix_acts"] + [p["ee"]] + p.get("suggestion", [])
    prefix_str = " \u2192 ".join(p["prefix_acts"])
    ee_str = p["ee"]
    trace_str = " \u2192 ".join(full)

    return (
        prefix_str,
        ee_str,
        trace_str,
        f"{idx + 1} / {len(proposals)}   |   Desirable: {len(desirable)}   Undesirable: {len(undesirable)}",
        des_str,
        undes_str,
        add_des_btn,
        add_undes_btn,
    )


def nav_next(state):
    if state is None or not state["proposals"]:
        return state, "", "", "", "", "(empty)", "(empty)", gr.Button(interactive=False), gr.Button(elem_id="undes-btn", variant="primary", interactive=False)
    idx = min(state["idx"] + 1, len(state["proposals"]))
    state["idx"] = idx
    return state, *_proposal_display(state["proposals"], idx, state["desirable"], state["undesirable"])


def nav_prev(state):
    if state is None or not state["proposals"]:
        return state, "", "", "", "", "(empty)", "(empty)", gr.Button(interactive=False), gr.Button(elem_id="undes-btn", variant="primary", interactive=False)
    idx = max(state["idx"] - 1, 0)
    state["idx"] = idx
    return state, *_proposal_display(state["proposals"], idx, state["desirable"], state["undesirable"])


def add_desirable(trace_text, state):
    if state is None or state["idx"] >= len(state["proposals"]):
        return state, "", "", "", "", "(empty)", "(empty)", gr.Button(interactive=False), gr.Button(elem_id="undes-btn", variant="primary", interactive=False)
    t = trace_text.strip()
    if not t:
        return state, *_proposal_display(state["proposals"], state["idx"], state["desirable"], state["undesirable"])
    p = state["proposals"][state["idx"]]
    if t not in state["desirable"]:
        if t in state["undesirable"]:
            state["undesirable"].remove(t)
        state["desirable"].append(t)
    idx2 = min(state["idx"] + 1, len(state["proposals"]))
    state["idx"] = idx2
    return state, *_proposal_display(state["proposals"], idx2, state["desirable"], state["undesirable"])


def add_undesirable(trace_text, state):
    if state is None or state["idx"] >= len(state["proposals"]):
        return state, "", "", "", "", "(empty)", "(empty)", gr.Button(interactive=False), gr.Button(elem_id="undes-btn", variant="primary", interactive=False)
    t = trace_text.strip()
    if not t:
        return state, *_proposal_display(state["proposals"], state["idx"], state["desirable"], state["undesirable"])
    p = state["proposals"][state["idx"]]
    if t not in state["undesirable"]:
        if t in state["desirable"]:
            state["desirable"].remove(t)
        state["undesirable"].append(t)
    idx2 = min(state["idx"] + 1, len(state["proposals"]))
    state["idx"] = idx2
    return state, *_proposal_display(state["proposals"], idx2, state["desirable"], state["undesirable"])


def manual_add_desirable(trace_text, state):
    if state is None or not trace_text.strip():
        if state is None:
            return state, "(empty)", "(empty)"
        des_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["desirable"])) if state["desirable"] else "(empty)"
        undes_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["undesirable"])) if state["undesirable"] else "(empty)"
        return state, des_str, undes_str
    t = trace_text.strip()
    if t not in state["desirable"]:
        if t in state["undesirable"]:
            state["undesirable"].remove(t)
        state["desirable"].append(t)
    des_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["desirable"])) if state["desirable"] else "(empty)"
    undes_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["undesirable"])) if state["undesirable"] else "(empty)"
    return state, des_str, undes_str


def manual_add_undesirable(trace_text, state):
    if state is None or not trace_text.strip():
        if state is None:
            return state, "(empty)", "(empty)"
        des_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["desirable"])) if state["desirable"] else "(empty)"
        undes_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["undesirable"])) if state["undesirable"] else "(empty)"
        return state, des_str, undes_str
    t = trace_text.strip()
    if t not in state["undesirable"]:
        if t in state["desirable"]:
            state["desirable"].remove(t)
        state["undesirable"].append(t)
    des_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["desirable"])) if state["desirable"] else "(empty)"
    undes_str = "\n".join(f"{i+1}. {t}" for i, t in enumerate(state["undesirable"])) if state["undesirable"] else "(empty)"
    return state, des_str, undes_str


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


def _build_undesirable_log(state):
    logM = EventLog()
    for s in state["undesirable"]:
        acts = [a.strip() for a in s.split("\u2192") if a.strip()]
        trace = Trace()
        for a in acts:
            ev = Event()
            ev[ACTIVITY_KEY] = a
            trace.append(ev)
        logM.append(trace)
    return logM


def rediscover_imbi(state, noise_threshold, ratio, progress=gr.Progress()):
    if state is None:
        raise gr.Error("Discover a model first.")

    logP = state["log"]
    logM = _build_undesirable_log(state)

    if len(logM) == 0:
        raise gr.Error("No undesirable traces collected. Add some traces to the undesirable log first.")

    size_par = len(logP) / len(logM)

    if not _imbi_available:
        raise gr.Error("Inductive Miner - bi is not available. Check the InductiveMiner_bi dependency.")

    progress(0.2, "Running Inductive Miner - bi...")
    net, im, fm = _inductive_miner.apply_bi(
        logP, logM,
        variant=_inductive_miner.Variants.IMbi,
        sup=noise_threshold,
        ratio=ratio,
        size_par=size_par,
    )

    progress(0.6, "Computing metrics...")
    log_fitness = pm4py.fitness_alignments(logP, net, im, fm, multi_processing=False)["log_fitness"]
    precision = pm4py.precision_alignments(logP, net, im, fm, multi_processing=False)
    denominator = log_fitness + precision
    f1 = 2 * (log_fitness * precision) / denominator if denominator != 0 else 0.0
    size = len(net.places) + len(net.transitions) + len(net.arcs)

    progress(0.8, "Rendering Petri net...")
    img_path = os.path.join(tempfile.gettempdir(), "petri_net_imbi_preview.png")
    pm4py.save_vis_petri_net(net, im, fm, img_path)

    metrics = (
        f"Fitness: {log_fitness:.4f}\n"
        f"Precision: {precision:.4f}\n"
        f"F1 Score: {f1:.4f}\n"
        f"Model Size: {size}\n"
        f"Undesirable traces used: {len(logM)} / {len(state['undesirable'])}"
    )

    return img_path, metrics


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
                noise_slider = gr.Slider(minimum=0.0, maximum=1.0, step=0.05, value=0.2, label="IMbi Support")
            with gr.Row():
                discover_btn = gr.Button("Discover with IMbi", variant="primary", size="lg")
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
                    des_add_input = gr.Textbox(label="Add trace manually (activities separated by →)", lines=2, placeholder="e.g. register request → examine casually → decide")
                    des_add_btn = gr.Button("+ Add to Desirable", variant="primary", size="sm")
                with gr.Column():
                    gr.Markdown("### Undesirable Traces")
                    undes_list = gr.Textbox(label="", lines=6, interactive=False)
                    undes_add_input = gr.Textbox(label="Add trace manually (activities separated by →)", lines=2, placeholder="e.g. register request → reject request")
                    undes_add_btn = gr.Button("+ Add to Undesirable", elem_id="undes-btn", variant="primary", size="sm")
            gr.Markdown("---")
            with gr.Row():
                export_des_btn = gr.Button("Export Desirable Log (.xes)", variant="secondary")
                export_undes_btn = gr.Button("Export Undesirable Log (.xes)", variant="secondary")
            with gr.Row():
                export_des_file = gr.File(label="Download Desirable Log", visible=True)
                export_undes_file = gr.File(label="Download Undesirable Log", visible=True)

        # ---- Tab 4: Rediscover with IMbi ----
        with gr.Tab("Rediscover"):
            gr.Markdown("### Rediscover using IMbi with undesirable log")
            with gr.Row():
                imbi_noise = gr.Slider(minimum=0.0, maximum=1.0, step=0.05, value=0.2, label="IMbi Support")
                imbi_ratio = gr.Slider(minimum=0.0, maximum=1.0, step=0.05, value=0.5, label="IMbi Ratio")
            with gr.Row():
                rediscover_btn = gr.Button("Rediscover with IMbi", variant="primary", size="lg")
            with gr.Row():
                imbi_metrics = gr.Textbox(label="Metrics", lines=6)
            with gr.Row():
                imbi_img = gr.Image(label="Rediscovered Petri Net", height=400)

    # --- event wiring ---

    discover_outputs = [
        petri_img, metrics_box,
        prefix_table, state_table,
        state,
        prefix_display, ee_display, trace_input, nav_info,
        des_list, undes_list,
        add_des_btn, add_undes_btn,
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
        inputs=[log_input, noise_slider],
        outputs=discover_outputs,
    )

    nav_outputs = [state, prefix_display, ee_display, trace_input, nav_info, des_list, undes_list, add_des_btn, add_undes_btn]

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

    des_add_btn.click(
        fn=manual_add_desirable,
        inputs=[des_add_input, state],
        outputs=[state, des_list, undes_list],
    )

    undes_add_btn.click(
        fn=manual_add_undesirable,
        inputs=[undes_add_input, state],
        outputs=[state, des_list, undes_list],
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

    rediscover_btn.click(
        fn=rediscover_imbi,
        inputs=[state, imbi_noise, imbi_ratio],
        outputs=[imbi_img, imbi_metrics],
    )


if __name__ == "__main__":
    demo.launch(
        share=False,
        theme=gr.themes.Soft(),
        css="#undes-btn { --button-primary-background-fill: #d32f2f; --button-primary-background-fill-hover: #b71c1c; --button-primary-text-color: white; } #undes-btn:hover { --button-primary-background-fill: #b71c1c; }",
    )
