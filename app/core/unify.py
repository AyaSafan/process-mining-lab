import pandas as pd


def unify_processarea(
    log: pd.DataFrame, threshold: float = 0.9
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Unify PROCESSAREA below threshold.

    For each OPERATION, find the dominant PROCESSAREA. If it accounts for
    more than ``threshold`` of occurrences, rewrite minority values to match.

    Returns
    -------
    unified : pd.DataFrame
        Copy of log with minority PROCESSAREA values rewritten.
    changes : pd.DataFrame
        Rows that were changed (for display).
    """
    log = log.copy()
    pa_col = "PROCESSAREA"
    act_col = "concept:name"

    before_areas = log.groupby(act_col)[pa_col].apply(
        lambda s: s.value_counts().to_dict()
    ).to_dict()

    changed_rows = []
    changed = 0
    for oper, grp in log.groupby(act_col):
        counts = grp[pa_col].value_counts()
        dominant = counts.index[0]
        frac = counts.iloc[0] / counts.sum()
        if frac >= threshold:
            mask = log[act_col] == oper
            minority = log.loc[mask, pa_col] != dominant
            changed += minority.sum()
            if minority.any():
                changed_events = log.loc[mask & minority].copy()
                changed_events["_old_area"] = changed_events[pa_col]
                changed_events["_new_area"] = dominant
                changed_rows.append(changed_events)
            log.loc[mask & minority, pa_col] = dominant

    after_areas = log.groupby(act_col)[pa_col].apply(
        lambda s: s.value_counts().to_dict()
    ).to_dict()

    changes_df = pd.concat(changed_rows) if changed_rows else pd.DataFrame()

    summary = {
        "changed": int(changed),
        "threshold": threshold,
        "areas_before": before_areas,
        "areas_after": after_areas,
    }

    return log, changes_df, summary
