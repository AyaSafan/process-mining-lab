import streamlit as st
import pandas as pd
import pm4py
from pathlib import Path
import io
import tempfile

from core.csv_to_xes import (
    detect_columns, csv_to_xes, get_summary, COL_CASE, COL_ACTIVITY, COL_TIMESTAMP,
)
from core.unify import unify_processarea
from core.split import split_traces
from core.group import run_activity_grouping, format_group_label
from core.mine import mine_group

st.set_page_config(page_title="PBB Miner", layout="wide")
st.title("PBB Miner")

PAGES = ["1. Upload & Convert", "2. Unify PROCESSAREA", "3. Split, Mine & Export"]

if "_page" in st.session_state:
    page = st.sidebar.radio("Navigate", PAGES, index=st.session_state["_page"])
else:
    page = st.sidebar.radio("Navigate", PAGES)

page_idx = PAGES.index(page)
st.session_state["_page"] = page_idx


def add_next_button():
    if page_idx < len(PAGES) - 1:
        st.divider()
        if st.button(f"Next: {PAGES[page_idx + 1]}", type="primary"):
            st.session_state["_page"] = page_idx + 1
            st.rerun()

# ── Page 1: Upload & Convert ─────────────────────────────────────────────────
if page == PAGES[0]:
    st.header("Upload CSV and convert to XES")
    uploaded = st.file_uploader("Upload a process flow CSV", type=["csv"])

    if uploaded:
        raw_bytes = uploaded.read()
        tmp = Path(tempfile.gettempdir()) / uploaded.name
        tmp.write_bytes(raw_bytes)

        sep = ";" if ";" in raw_bytes.decode("utf-8", errors="ignore").split("\n")[0] else ","
        peek = pd.read_csv(tmp, sep=sep, dtype=str, nrows=5)
        detected = detect_columns(peek)

        st.subheader("Column Mapping")
        st.caption("Auto-detected columns. Adjust if needed.")
        cols = peek.columns.tolist()
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            case_col = st.selectbox("Case ID", cols, index=cols.index(detected["case"]) if detected["case"] in cols else 0)
        with col2:
            activity_col = st.selectbox("Activity", cols, index=cols.index(detected["activity"]) if detected["activity"] in cols else 0)
        with col3:
            order_col = st.selectbox("Order", cols, index=cols.index(detected["order"]) if detected["order"] in cols else 0)
        with col4:
            pa_col = st.selectbox("Process Area", ["(none)"] + cols,
                                  index=(cols.index(detected["process_area"]) + 1) if detected.get("process_area") in cols else 0)

        if st.button("Convert to XES"):
            mapping = {
                "case": case_col,
                "activity": activity_col,
                "order": order_col,
                "process_area": pa_col if pa_col != "(none)" else None,
            }
            with st.spinner("Converting..."):
                events, log = csv_to_xes(tmp, mapping)
                summary = get_summary(events)

            st.session_state["events"] = events
            st.session_state["log"] = log
            st.session_state["summary"] = summary

            st.success(f"Converted: {summary['traces']} traces, {summary['events']} events")
            st.dataframe(events.head(20), use_container_width=True)

    if "summary" in st.session_state:
        st.subheader("Current Log Summary")
        s = st.session_state["summary"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Traces", s["traces"])
        c2.metric("Events", s["events"])
        c3.metric("Unique Activities", len(s["activities"]))

    add_next_button()

# ── Page 2: Unify PROCESSAREA ────────────────────────────────────────────────
elif page == PAGES[1]:
    st.header("Unify PROCESSAREA below threshold")
    st.markdown(
        "For each **operation**, find the most common PROCESSAREA across all traces. "
        "If that dominant area accounts for more than the threshold fraction of occurrences, "
        "rewrite all minority instances to match. This cleans up noisy PROCESSAREA assignments "
        "before splitting — e.g. if operation `1035` is `DDE3` in 95% of traces but `PRE` in 5%, "
        "those 5% get reassigned to `DDE3`."
    )
    if "events" not in st.session_state:
        st.info("Complete step 1 first.")
    else:
        events = st.session_state["events"]
        if "PROCESSAREA" not in events.columns:
            st.warning("No PROCESSAREA column found in the data.")
        else:
            threshold = st.slider("Dominance threshold", 0.5, 1.0, 0.9, 0.05,
                                  help="Operations where one PROCESSAREA accounts for this fraction get unified.")

            if st.button("Run Unification"):
                with st.spinner("Unifying..."):
                    unified, changes, summary = unify_processarea(events, threshold)
                    st.session_state["events"] = unified
                    st.session_state["unify_changes"] = changes
                    st.session_state["unify_summary"] = summary

            if "unify_summary" in st.session_state:
                sm = st.session_state["unify_summary"]
                st.metric("Events changed", sm["changed"])

                if not st.session_state["unify_changes"].empty:
                    st.subheader("Changed events")
                    st.dataframe(
                        st.session_state["unify_changes"][
                            ["concept:name", "PROCESSAREA", "_old_area", "_new_area"]
                        ].drop_duplicates(),
                        use_container_width=True,
                    )

    add_next_button()

# ── Page 3: Split, Mine & Export ──────────────────────────────────────────────
elif page == PAGES[2]:
    st.header("Split traces, Mine BPMNs & Export")
    st.markdown(
        "**Step 1 — Split at boundaries:** Each trace is split into sublogs at every "
        "PROCESSAREA change. A trace like `PRE → DDE3 → LC3 → PRE` becomes 4 sublogs. "
        "Single-operation outliers (e.g. one `DDE3` surrounded by `PRE`) are smoothed and "
        "don't create a split.\n\n"
        "**Step 2 — Boundary Change PBB:** A Split Miner BPMN is discovered for each "
        "(segment, PROCESSAREA) group (e.g. `seg0_PRE`, `seg1_DDE3`).\n\n"
        "**Step 3 — Variant Based PBB:** Within each boundary group, sublogs are "
        "further clustered by activity-set compatibility (order/repetition ignored, "
        "but different activities create separate groups). A BPMN is discovered for each cluster."
    )
    if "events" not in st.session_state:
        st.info("Complete steps 1-2 first.")
    else:
        events = st.session_state["events"]
        if "PROCESSAREA" not in events.columns:
            st.warning("No PROCESSAREA column found in the data.")
        else:
            if st.button("Run Split & Mine"):
                # --- Split at boundaries ---
                with st.spinner("Splitting traces at PROCESSAREA boundaries..."):
                    split_df, split_summary = split_traces(events)
                    st.session_state["split_df"] = split_df
                    st.session_state["split_summary"] = split_summary

                # --- Activity-set grouping ---
                with st.spinner("Grouping sublogs by activity set..."):
                    activity_groups = run_activity_grouping(split_df)
                    st.session_state["activity_groups"] = activity_groups

                # --- Mine boundary change PBBs ---
                orig_area_map = {}
                for oper, grp in split_df.groupby("concept:name"):
                    orig_area_map[oper] = grp["PROCESSAREA"].value_counts().index[0]

                boundary_results = {}
                groups_boundary = {}
                for (seg_idx, area), grp in split_df.groupby(["_seg_idx", "PROCESSAREA"]):
                    groups_boundary[(seg_idx, area)] = grp["case:concept:name"].unique().tolist()

                progress = st.progress(0)
                total = len(groups_boundary)
                for i, ((seg_idx, area), case_ids) in enumerate(groups_boundary.items()):
                    mask = split_df["case:concept:name"].isin(case_ids)
                    sub_df = split_df[mask].copy()
                    label = f"{area}_seg{seg_idx}"
                    with st.spinner(f"Mining {label}..."):
                        result = mine_group(sub_df, orig_area_map)
                    boundary_results[label] = result
                    progress.progress((i + 1) / (total * 2))

                st.session_state["boundary_results"] = boundary_results

                # --- Mine variant based PBBs ---
                activity_results = {}
                groups_activity = {}
                for (seg_idx, area), act_groups in activity_groups.items():
                    for act_set, case_ids in act_groups.items():
                        label = format_group_label(area, seg_idx, act_set)
                        groups_activity[label] = case_ids

                for i, (label, case_ids) in enumerate(groups_activity.items()):
                    mask = split_df["case:concept:name"].isin(case_ids)
                    sub_df = split_df[mask].copy()
                    with st.spinner(f"Mining {label}..."):
                        result = mine_group(sub_df, orig_area_map)
                    activity_results[label] = result
                    progress.progress((total + i + 1) / (total + len(groups_activity)))

                st.session_state["activity_results"] = activity_results
                st.success(f"Done: {len(boundary_results)} boundary change PBBs, {len(activity_results)} variant based PBBs")

            # --- Display results & export ---
            has_boundary = "boundary_results" in st.session_state
            has_activity = "activity_results" in st.session_state
            if has_boundary or has_activity:
                all_results = {}
                if has_boundary:
                    for k, v in st.session_state["boundary_results"].items():
                        all_results[f"[Boundary Change] {k}"] = v
                if has_activity:
                    for k, v in st.session_state["activity_results"].items():
                        all_results[f"[Variant Based] {k}"] = v

                selected = []
                for label, result in all_results.items():
                    with st.expander(f"**{label}** — Fitness: {result['fitness']:.4f} | Precision: {result['precision']:.4f} | {result['n_sublogs']} sublogs", expanded=True):
                        st.write("**Variants allowed:**")
                        for seq, count in result["variants_allowed"]:
                            st.write(f"  [{count:3d}x] {seq}")
                        if result["variants_not_allowed"]:
                            st.write("**Variants not allowed:**")
                            for seq, count in result["variants_not_allowed"]:
                                st.write(f"  [{count:3d}x] {seq}")
                        try:
                            tmp_bpmn = Path(tempfile.gettempdir()) / f"bpmn_{label}.png"
                            pm4py.save_vis_bpmn(result["bpmn"], str(tmp_bpmn))
                            st.image(str(tmp_bpmn))
                        except Exception as e:
                            st.warning(f"Could not render BPMN: {e}")
                        if st.checkbox("Select for export", key=f"sel_{label}"):
                            selected.append(label)

                if selected:
                    st.divider()
                    st.subheader("Export")
                    for label in selected:
                        bpmn = all_results[label]["bpmn"]
                        safe_name = label.replace("[", "").replace("]", "").replace(" ", "_")
                        tmp_xml = Path(tempfile.gettempdir()) / f"{safe_name}.bpmn"
                        pm4py.write_bpmn(bpmn, str(tmp_xml))
                        bpmn_xml = tmp_xml.read_bytes()
                        st.download_button(
                            label=f"Download {safe_name}.bpmn",
                            data=bpmn_xml,
                            file_name=f"{safe_name}.bpmn",
                            mime="application/xml",
                            key=f"dl_{safe_name}",
                        )
