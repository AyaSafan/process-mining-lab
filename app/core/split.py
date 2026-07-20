import pandas as pd


def smooth_outliers(areas: list[str]) -> list[str]:
    """Smooth single-operation outliers.

    If an operation differs from both neighbours (which agree),
    treat it as the neighbour value for split-point detection.
    """
    smoothed = list(areas)
    for i in range(1, len(smoothed) - 1):
        if (
            smoothed[i] != smoothed[i - 1]
            and smoothed[i] != smoothed[i + 1]
            and smoothed[i - 1] == smoothed[i + 1]
        ):
            smoothed[i] = smoothed[i - 1]
    return smoothed


def split_traces(log: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Split each trace at PROCESSAREA change boundaries.

    Returns
    -------
    split_df : pd.DataFrame
        All events with segment case IDs and metadata.
    summary : dict
        Statistics about the split.
    """
    rows = []
    for case_id, trace in log.groupby("case:concept:name"):
        trace = trace.sort_values("ROUTEOPERORDER").reset_index(drop=True)
        areas = trace["PROCESSAREA"].tolist()

        smoothed = smooth_outliers(areas)

        split_at = set()
        for i in range(1, len(smoothed)):
            if smoothed[i] != smoothed[i - 1]:
                split_at.add(i)

        seg_idx = 0
        for i, (_, ev) in enumerate(trace.iterrows()):
            if i in split_at:
                seg_idx += 1
            rows.append({
                "case:concept:name": f"{case_id}_seg{seg_idx}",
                "concept:name": ev["concept:name"],
                "time:timestamp": ev["time:timestamp"],
                "ROUTEOPERORDER": ev["ROUTEOPERORDER"],
                "PROCESSAREA": smoothed[i],
                "_orig_case": case_id,
                "_seg_idx": seg_idx,
            })

    split_df = pd.DataFrame(rows)

    boundary_summary = {}
    for (seg_idx, area), grp in split_df.groupby(["_seg_idx", "PROCESSAREA"]):
        key = f"seg{seg_idx} ({area})"
        boundary_summary[key] = {
            "seg_idx": int(seg_idx),
            "area": area,
            "n_sublogs": int(grp["case:concept:name"].nunique()),
            "n_events": int(len(grp)),
        }

    summary = {
        "original_traces": int(log["case:concept:name"].nunique()),
        "total_sublogs": int(split_df["case:concept:name"].nunique()),
        "boundaries": boundary_summary,
    }

    return split_df, summary
