import pandas as pd
import pm4py
from pathlib import Path


DEFAULT_COLUMNS = [
    "ROUTERELATION", "FACILITY", "ROUTEID", "ROUTE",
    "ROUTEDESCRIPTION", "ROUTEOPERORDER", "OPERATION", "PROCESSAREA",
]

COL_CASE = "case:concept:name"
COL_ACTIVITY = "concept:name"
COL_TIMESTAMP = "time:timestamp"


def detect_delimiter(csv_path: Path) -> str:
    first_line = open(csv_path, encoding="utf-8").readline()
    return ";" if ";" in first_line else ","


def detect_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Try to auto-detect required columns. Returns mapping of required -> found."""
    required = {
        "case": "ROUTEID",
        "activity": "OPERATION",
        "order": "ROUTEOPERORDER",
        "process_area": "PROCESSAREA",
    }
    detected = {}
    for key, col in required.items():
        detected[key] = col if col in df.columns else None
    return detected


def build_route_filter(labels_csv: Path, target_labels: set[str]) -> pd.DataFrame:
    labels_df = pd.read_csv(labels_csv, sep=";", dtype=str, skiprows=1).fillna("")
    filtered = labels_df[labels_df["PROCESSFLOW_LABEL"].isin(target_labels)]
    return (
        filtered[["FACILITY_NAME", "ROUTE_NAME"]]
        .drop_duplicates()
        .rename(columns={"FACILITY_NAME": "FACILITY", "ROUTE_NAME": "ROUTE"})
    )


def csv_to_xes(
    csv_path: Path,
    col_mapping: dict[str, str],
    route_filter: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, object]:
    sep = detect_delimiter(csv_path)
    df = pd.read_csv(csv_path, sep=sep, dtype=str).fillna("")

    case_col = col_mapping["case"]
    activity_col = col_mapping["activity"]
    order_col = col_mapping["order"]
    pa_col = col_mapping.get("process_area")

    keep_cols = [c for c in [case_col, activity_col, order_col, pa_col] if c and c in df.columns]
    if pa_col and pa_col not in keep_cols:
        keep_cols.append(pa_col)
    df = df[keep_cols].copy()

    if route_filter is not None and "FACILITY" in df.columns and "ROUTE" in df.columns:
        before = len(df)
        df = df.merge(route_filter, on=["FACILITY", "ROUTE"], how="inner")
        print(f"filtered {before - len(df)} rows outside target labels")

    before = len(df)
    df = df.drop_duplicates()
    if len(df) < before:
        print(f"dropped {before - len(df)} duplicate row(s)")

    key_cols = [c for c in [case_col, activity_col, order_col] if c in df.columns]
    if pa_col and pa_col in df.columns:
        before = len(df)
        df["_pa_freq"] = df.groupby(key_cols)[pa_col].transform(
            lambda s: s.map(s.value_counts())
        )
        df = (
            df.sort_values("_pa_freq", ascending=False)
            .drop_duplicates(subset=key_cols, keep="first")
            .drop(columns="_pa_freq")
        )
        if len(df) < before:
            print(f"resolved {before - len(df)} PROCESSAREA conflicts")

    df[order_col] = pd.to_numeric(df[order_col], errors="coerce").fillna(0).astype(int)
    df = df.sort_values([case_col, order_col]).reset_index(drop=True)

    step_per_trace = df.groupby(case_col).cumcount()
    df[COL_TIMESTAMP] = pd.Timestamp("2024-01-01") + pd.to_timedelta(step_per_trace * 2 + 8, unit="h")

    df[COL_CASE] = df[case_col]
    df[COL_ACTIVITY] = df[activity_col]

    keep = [COL_CASE, COL_ACTIVITY, COL_TIMESTAMP, order_col]
    if pa_col and pa_col in df.columns:
        keep.append(pa_col)
    events = df[keep].copy()

    log = pm4py.convert_to_event_log(events)
    return events, log


def get_summary(events: pd.DataFrame) -> dict:
    return {
        "traces": events[COL_CASE].nunique(),
        "events": len(events),
        "activities": sorted(events[COL_ACTIVITY].unique().tolist()),
    }
