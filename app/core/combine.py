import pandas as pd
import pm4py
from pm4py.objects.bpmn.obj import BPMN
from core.mine import mine_group


def _clone_node(node):
    """Create a new BPMN node of the same type."""
    cls = node.__class__
    return cls(name=node.get_name())


def _find_start(bpmn):
    for n in bpmn.get_nodes():
        if isinstance(n, BPMN.StartEvent):
            return n
    return None


def _find_end(bpmn):
    for n in bpmn.get_nodes():
        if isinstance(n, BPMN.NormalEndEvent):
            return n
    return None


def _predecessors(bpmn, node):
    """Return original nodes that flow into *node*."""
    return [f.get_source() for f in bpmn.get_flows() if f.get_target() is node]


def _successors(bpmn, node):
    """Return original nodes that flow out of *node*."""
    return [f.get_target() for f in bpmn.get_flows() if f.get_source() is node]


def combine_horizontal(bpmns):
    """
    Chain multiple BPMNs while preserving their full internal structure.

    For each pair of consecutive BPMNs the end event of the first and the
    start event of the second are removed.  Their predecessors / successors
    are bridged so the flow goes straight from one model into the next.
    """
    if not bpmns:
        return None
    if len(bpmns) == 1:
        return bpmns[0]

    combined = BPMN()
    prev_end_cloned = None
    prev_end_preds_cloned = None

    for i, bpmn in enumerate(bpmns):
        orig_start = _find_start(bpmn)
        orig_end = _find_end(bpmn)

        # ---- clone every node ----
        node_map = {}
        for node in bpmn.get_nodes():
            node_map[node] = _clone_node(node)
            combined.add_node(node_map[node])

        # ---- clone every flow ----
        for flow in bpmn.get_flows():
            combined.add_flow(
                BPMN.Flow(node_map[flow.get_source()], node_map[flow.get_target()])
            )

        # ---- bridge from previous BPMN ----
        if prev_end_cloned is not None:
            start_succs_cloned = [node_map[s] for s in _successors(bpmn, orig_start)]
            for pred in prev_end_preds_cloned:
                for succ in start_succs_cloned:
                    combined.add_flow(BPMN.Flow(pred, succ))

            # remove the intermediate boundary nodes and their old flows
            _remove_node_and_flows(combined, prev_end_cloned)
            _remove_node_and_flows(combined, node_map[orig_start])

        # ---- prepare for next iteration ----
        if i < len(bpmns) - 1:
            prev_end_cloned = node_map[orig_end]
            prev_end_preds_cloned = [node_map[p] for p in _predecessors(bpmn, orig_end)]
        else:
            prev_end_cloned = None
            prev_end_preds_cloned = None

    return combined


def _remove_node_and_flows(bpmn_obj, node):
    """Remove a node and every flow that references it."""
    flows_to_remove = [
        f for f in bpmn_obj.get_flows()
        if f.get_source() is node or f.get_target() is node
    ]
    for f in flows_to_remove:
        bpmn_obj.get_flows().discard(f)
    bpmn_obj.get_nodes().discard(node)


def combine_vertical(
    sublogs: list[pd.DataFrame],
    orig_area_map: dict[str, str] | None = None,
) -> dict:
    """Merge sublogs and re-mine a BPMN."""
    merged = pd.concat(sublogs, ignore_index=True)
    return mine_group(merged, orig_area_map)
