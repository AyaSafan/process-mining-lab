import gradio as gr
import pm4py
from shared import _imbi_available, _inductive_miner
from discovery import discover, rediscover_imbi
from investigate import investigate_tree
from log_utils import export_desirable, export_undesirable, format_trace_list

# --- proposal navigation helpers ---

def _proposal_display(proposals, idx, desirable, undesirable):
    des_str = format_trace_list(desirable)
    undes_str = format_trace_list(undesirable)

    finished = not proposals or idx >= len(proposals)
    add_des_btn = gr.Button(interactive=not finished)
    add_undes_btn = gr.Button(elem_id="undes-btn", variant="primary", interactive=not finished)

    if finished:
        return (
            "* Done - all proposals reviewed *",
            "",
            "",
            f"0 / 0   |   Desirable: {len(desirable)}   Undesirable: {len(undesirable)}",
            des_str, undes_str,
            add_des_btn, add_undes_btn,
        )
    p = proposals[idx]
    full = p["prefix_acts"] + [p["ee"]] + p.get("suggestion", [])
    return (
        " \u2192 ".join(p["prefix_acts"]),
        p["ee"],
        " \u2192 ".join(full),
        f"{idx + 1} / {len(proposals)}   |   Desirable: {len(desirable)}   Undesirable: {len(undesirable)}",
        des_str, undes_str,
        add_des_btn, add_undes_btn,
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


def _add_trace(trace_text, state, to_list, from_list):
    if state is None or state["idx"] >= len(state["proposals"]):
        return state, "", "", "", "", "(empty)", "(empty)", gr.Button(interactive=False), gr.Button(elem_id="undes-btn", variant="primary", interactive=False)
    t = trace_text.strip()
    if not t:
        return state, *_proposal_display(state["proposals"], state["idx"], state["desirable"], state["undesirable"])
    if t not in to_list:
        if t in from_list:
            from_list.remove(t)
        to_list.append(t)
    idx2 = min(state["idx"] + 1, len(state["proposals"]))
    state["idx"] = idx2
    return state, *_proposal_display(state["proposals"], idx2, state["desirable"], state["undesirable"])


def add_desirable(trace_text, state):
    return _add_trace(trace_text, state, state["desirable"], state["undesirable"])


def add_undesirable(trace_text, state):
    return _add_trace(trace_text, state, state["undesirable"], state["desirable"])


def _manual_add(trace_text, state, to_list, from_list):
    if state is None or not trace_text.strip():
        if state is None:
            return state, "(empty)", "(empty)"
        return state, format_trace_list(state["desirable"]), format_trace_list(state["undesirable"])
    t = trace_text.strip()
    if t not in to_list:
        if t in from_list:
            from_list.remove(t)
        to_list.append(t)
    return state, format_trace_list(state["desirable"]), format_trace_list(state["undesirable"])


def manual_add_desirable(trace_text, state):
    return _manual_add(trace_text, state, state["desirable"], state["undesirable"])


def manual_add_undesirable(trace_text, state):
    return _manual_add(trace_text, state, state["undesirable"], state["desirable"])


# --- Gradio app ---

with gr.Blocks(title="Escaping Edges Reviewer") as demo:
    state = gr.State()
    gr.Markdown("# Escaping Edges Reviewer")
    gr.Markdown("Discover a process model, analyze escaping edges, review proposals, and build desirable/undesirable logs.")

    with gr.Tabs():
        # ---- Tab 1: Investigate ----
        with gr.Tab("Investigate"):
            gr.Markdown("Explore the event log: view statistics, the discovered process tree, and operator counts.")
            with gr.Row():
                investigate_input = gr.File(label="Event Log (.xes)", file_types=[".xes"], type="filepath")
            with gr.Row():
                investigate_sup = gr.Slider(minimum=0.0, maximum=1.0, step=0.05, value=0.2, label="IMbi Support")
            with gr.Row():
                investigate_btn = gr.Button("Show Process Tree", variant="primary", size="lg")
            with gr.Row():
                investigate_stats = gr.Textbox(label="Log Statistics", lines=3, interactive=False)
            with gr.Row():
                investigate_tree_info = gr.Textbox(label="Operator Counts", lines=5, interactive=False)
            with gr.Row():
                investigate_img = gr.Image(label="Process Tree", height=500)

        # ---- Tab 2: Load & Discover ----
        with gr.Tab("Load & Discover"):
            gr.Markdown(
                "**What is IMbi?**  \n"
                "Inductive Miner with bias \u2014 discovers a process model using desirable behavior (log\u207a) "
                "while penalizing undesirable behavior (log\u207b).\n"
                "\n"
                "**Support** (0\u20131) \u2014 noise tolerance. Higher \u2192 more general model.  \n"
                "**Ratio** (0\u20131) \u2014 how strongly undesirable traces are penalized. Higher \u2192 more aggressive suppression.\n"
                "\n"
                "**Workflow:**  \n"
                "1. Load a log and **Discover** (no negative examples yet).  \n"
                "2. Review proposals and collect desirable/undesirable traces in **Proposals Review**.  \n"
                "3. Rediscover with bias in **Rediscover with IMbi** tab."
            )
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

        # ---- Tab 3: Escaping Edges ----
        with gr.Tab("Escaping Edges"):
            gr.Markdown("### Prefix-level Escaping Edges (sorted by Impact)")
            prefix_table = gr.Dataframe(
                headers=["Prefix", "Support", "|EE|", "Impact", "Escaping Edges"],
                column_count=5, interactive=False, wrap=True,
            )
            gr.Markdown("### State-grouped Escaping Edges (sorted by Support)")
            state_table = gr.Dataframe(
                headers=["Example Prefix", "EE", "Support", "State (Activated Transitions)"],
                column_count=4, interactive=False, wrap=True,
            )

        # ---- Tab 4: Proposals Review ----
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
                    des_add_input = gr.Textbox(label="Add trace manually (activities separated by \u2192)", lines=2, placeholder="e.g. register request \u2192 examine casually \u2192 decide")
                    des_add_btn = gr.Button("+ Add to Desirable", variant="primary", size="sm")
                with gr.Column():
                    gr.Markdown("### Undesirable Traces")
                    undes_list = gr.Textbox(label="", lines=6, interactive=False)
                    undes_add_input = gr.Textbox(label="Add trace manually (activities separated by \u2192)", lines=2, placeholder="e.g. register request \u2192 reject request")
                    undes_add_btn = gr.Button("+ Add to Undesirable", elem_id="undes-btn", variant="primary", size="sm")
            gr.Markdown("---")
            with gr.Row():
                export_des_btn = gr.Button("Export Desirable Log (.xes)", variant="secondary")
                export_undes_btn = gr.Button("Export Undesirable Log (.xes)", variant="secondary")
            with gr.Row():
                export_des_file = gr.File(label="Download Desirable Log", visible=True)
                export_undes_file = gr.File(label="Download Undesirable Log", visible=True)

        # ---- Tab 5: Rediscover with IMbi ----
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

    def _discover_wrapper(log_path, noise):
        img, metrics, pdf, sdf, app_state = discover(log_path, noise, gr.Progress())
        dv = _proposal_display(app_state["proposals"], 0, [], [])
        return (img, metrics, pdf, sdf, app_state, *dv)

    view_btn.click(
        fn=lambda s: (
            pm4py.view_petri_net(s["net"], s["im"], s["fm"]) if s and s.get("net") else None,
            gr.HTML(visible=False),
        ),
        inputs=[state],
        outputs=[view_html],
    )

    discover_btn.click(
        fn=_discover_wrapper,
        inputs=[log_input, noise_slider],
        outputs=discover_outputs,
    )

    nav_outputs = [state, prefix_display, ee_display, trace_input, nav_info, des_list, undes_list, add_des_btn, add_undes_btn]

    prev_btn.click(fn=nav_prev, inputs=[state], outputs=nav_outputs)
    next_btn.click(fn=nav_next, inputs=[state], outputs=nav_outputs)

    add_des_btn.click(fn=add_desirable, inputs=[trace_input, state], outputs=nav_outputs)
    add_undes_btn.click(fn=add_undesirable, inputs=[trace_input, state], outputs=nav_outputs)

    des_add_btn.click(fn=manual_add_desirable, inputs=[des_add_input, state], outputs=[state, des_list, undes_list])
    undes_add_btn.click(fn=manual_add_undesirable, inputs=[undes_add_input, state], outputs=[state, des_list, undes_list])

    export_des_btn.click(fn=export_desirable, inputs=[state], outputs=[export_des_file])
    export_undes_btn.click(fn=export_undesirable, inputs=[state], outputs=[export_undes_file])

    investigate_btn.click(
        fn=lambda p, s: investigate_tree(p, s, gr.Progress()),
        inputs=[investigate_input, investigate_sup],
        outputs=[investigate_img, investigate_stats, investigate_tree_info],
    )

    rediscover_btn.click(
        fn=lambda st, n, r: rediscover_imbi(st, n, r, gr.Progress()),
        inputs=[state, imbi_noise, imbi_ratio],
        outputs=[imbi_img, imbi_metrics],
    )


if __name__ == "__main__":
    demo.launch(
        share=False,
        theme=gr.themes.Soft(),
        css="#undes-btn { --button-primary-background-fill: #d32f2f; --button-primary-background-fill-hover: #b71c1c; --button-primary-text-color: white; } #undes-btn:hover { --button-primary-background-fill: #b71c1c; }",
    )
