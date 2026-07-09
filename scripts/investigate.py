import os
import tempfile
import graphviz
import pm4py
from pm4py.objects.conversion.log import converter as log_converter
from pm4py.objects.log.obj import EventLog
from shared import _imbi_available, _inductive_miner


def investigate_tree(log_path, sup, progress=None):
    if not log_path or not log_path.endswith(".xes"):
        raise ValueError("Please select a .xes file.")

    if progress:
        progress(0.2, "Loading log...")
    event_log = log_converter.apply(pm4py.read_xes(log_path))

    if progress:
        progress(0.4, "Computing statistics...")
    variants = pm4py.get_variants(event_log)
    n_traces = len(event_log)
    n_variants = len(variants)
    n_activities = len(pm4py.get_event_attribute_values(event_log, "concept:name"))
    stats = (
        f"Traces: {n_traces}\n"
        f"Variants: {n_variants}\n"
        f"Activities: {n_activities}"
    )

    if not _imbi_available:
        raise RuntimeError("Inductive Miner - bi is not available.")

    if progress:
        progress(0.6, "Discovering process tree...")
    empty_log = EventLog()
    imbi_mod = _inductive_miner.Variants.IMbi.value
    tree = imbi_mod.apply_tree(
        event_log, empty_log,
        sup=sup,
        ratio=0,
        size_par=0,
    )

    if progress:
        progress(0.8, "Rendering tree...")
    img_path = os.path.join(tempfile.gettempdir(), "process_tree_preview.png")

    viz = graphviz.Graph("pt", engine="dot", graph_attr={"bgcolor": "white", "rankdir": "LR"})
    viz.attr("node", shape="ellipse", fixedsize="false", fontname="Arial")

    op_fills = {
        "PARALLEL": "#2E86C1",
        "LOOP": "#8E44AD",
    }

    def _add_node(node):
        nid = str(id(node))
        if node.operator is not None:
            op_name = node.operator.name
            symbol = str(node.operator)
            color = op_fills.get(op_name)
            if color:
                viz.node(nid, symbol, style="filled", fillcolor=color, fontcolor="white", shape="box", fontsize="15")
            else:
                viz.node(nid, symbol, shape="box", fontsize="15")
            for child in node.children:
                _add_node(child)
                viz.edge(nid, str(id(child)))
        elif node.label is not None:
            viz.node(nid, str(node.label), shape="ellipse", fontsize="15")
        else:
            viz.node(nid, "tau", style="filled", fillcolor="black", shape="point", width="0.075")

    _add_node(tree)
    viz.format = "png"
    img_path_raw = os.path.join(tempfile.gettempdir(), "process_tree_preview")
    viz.render(img_path_raw, cleanup=True)
    img_path = img_path_raw + ".png"

    def _count_ops(node, counts):
        if node is None:
            return
        if node.operator is not None:
            op_name = node.operator.name
            counts[op_name] = counts.get(op_name, 0) + 1
        for child in node.children:
            _count_ops(child, counts)

    op_counts = {}
    _count_ops(tree, op_counts)
    tree_info = (
        f"Parallel: {op_counts.get('PARALLEL', 0)}\n"
        f"Loop: {op_counts.get('LOOP', 0)}\n"
        f"Sequence: {op_counts.get('SEQUENCE', 0)}\n"
        f"XOR: {op_counts.get('XOR', 0)}\n"
        f"Total operators: {sum(op_counts.values())}"
    )

    return img_path, stats, tree_info
