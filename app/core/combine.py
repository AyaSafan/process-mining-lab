import pandas as pd
import pm4py
from pm4py.objects.bpmn.obj import BPMN
from core.mine import mine_group


def _clone_node(node):
    return node.__class__(name=node.get_name())


def _find_start(bpmn):
    return next((n for n in bpmn.get_nodes()
                 if isinstance(n, BPMN.StartEvent)), None)


def _find_end(bpmn):
    return next((n for n in bpmn.get_nodes()
                 if isinstance(n, BPMN.EndEvent)), None)


def _predecessors(bpmn, node):
    return [f.get_source() for f in bpmn.get_flows()
            if f.get_target() == node]


def _successors(bpmn, node):
    return [f.get_target() for f in bpmn.get_flows()
            if f.get_source() == node]


def combine_horizontal(bpmns):
    """
    Chain BPMNs while preserving their internal structure.
    """

    if not bpmns:
        return None

    if len(bpmns) == 1:
        return bpmns[0]

    combined = BPMN()

    # cloned predecessors of the previous BPMN's end event
    previous_end_nodes = None

    for i, bpmn in enumerate(bpmns):

        first = i == 0
        last = i == len(bpmns) - 1

        start = _find_start(bpmn)
        end = _find_end(bpmn)

        node_map = {}

        # --------------------
        # Clone nodes
        # --------------------
        for node in bpmn.get_nodes():

            if not first and node == start:
                continue

            if not last and node == end:
                continue

            clone = _clone_node(node)
            node_map[node] = clone
            combined.add_node(clone)

        # --------------------
        # Clone internal flows
        # --------------------
        for flow in bpmn.get_flows():

            src = flow.get_source()
            tgt = flow.get_target()

            if src not in node_map or tgt not in node_map:
                continue

            combined.add_flow(
                BPMN.Flow(
                    node_map[src],
                    node_map[tgt]
                )
            )

        # --------------------
        # Connect previous BPMN
        # --------------------
        if previous_end_nodes is not None:

            for succ in _successors(bpmn, start):
                if succ in node_map:
                    for pred in previous_end_nodes:
                        combined.add_flow(
                            BPMN.Flow(pred, node_map[succ])
                        )

        # --------------------
        # Save predecessors of end
        # --------------------
        if not last:
            previous_end_nodes = [
                node_map[p]
                for p in _predecessors(bpmn, end)
                if p in node_map
            ]
        else:
            previous_end_nodes = None

    return combined


def combine_vertical(
    sublogs: list[pd.DataFrame],
    orig_area_map: dict[str, str] | None = None,
) -> dict:
    """Merge sublogs and re-mine a BPMN."""
    prefixed = []
    for i, df in enumerate(sublogs):
        tmp = df.copy()
        tmp["case:concept:name"] = tmp["case:concept:name"].astype(str) + f"__sub{i}"
        prefixed.append(tmp)
    merged = pd.concat(prefixed, ignore_index=True)
    return mine_group(merged, orig_area_map)
