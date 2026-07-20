import pandas as pd
from itertools import combinations


def compute_activity_set(df: pd.DataFrame) -> frozenset[str]:
    return frozenset(df["concept:name"].unique())


def find_maximal_sets(activity_sets: set[frozenset]) -> list[frozenset]:
    """Return activity sets that are not proper subsets of any other."""
    maximal = []
    for s in activity_sets:
        if not any(s < other for other in activity_sets):
            maximal.append(s)
    return sorted(maximal, key=lambda s: (-len(s), sorted(s)))


def group_sublogs_by_activity_set(
    sublogs_dict: dict[str, pd.DataFrame],
) -> tuple[dict[frozenset, list[str]], dict[str, frozenset]]:
    """Group sublogs by activity-set compatibility.

    Returns
    -------
    groups : dict[frozenset -> list[str]]
        Mapping from maximal activity set to list of sublog case IDs.
    act_map : dict[str -> frozenset]
        Mapping from sublog case ID to its activity set.
    """
    act_map = {cid: compute_activity_set(df) for cid, df in sublogs_dict.items()}
    unique_sets = set(act_map.values())
    maximal = find_maximal_sets(unique_sets)

    groups = {ms: [] for ms in maximal}
    for cid, act_set in act_map.items():
        for ms in maximal:
            if act_set <= ms:
                groups[ms].append(cid)

    return groups, act_map


def run_activity_grouping(
    split_df: pd.DataFrame,
) -> dict[tuple[int, str], dict[frozenset, list[str]]]:
    """Run activity-set grouping per (seg_idx, PROCESSAREA)."""
    activity_groups = {}

    for (seg_idx, area), seg_grp in split_df.groupby(["_seg_idx", "PROCESSAREA"]):
        sublogs_dict = {}
        for cid, trace_grp in seg_grp.groupby("case:concept:name"):
            sublogs_dict[cid] = trace_grp.sort_values("ROUTEOPERORDER")

        groups, _ = group_sublogs_by_activity_set(sublogs_dict)
        activity_groups[(seg_idx, area)] = groups

    return activity_groups


def format_group_label(area: str, seg_idx: int, act_set: frozenset) -> str:
    acts = ",".join(sorted(act_set))
    return f"{area}_seg{seg_idx}_{{{acts}}}"
