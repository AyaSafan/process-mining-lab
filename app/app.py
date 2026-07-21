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
from core.combine import combine_horizontal, combine_vertical

st.set_page_config(page_title="PBB Miner", layout="wide")
st.title("PBB Miner")

PAGES = ["1. Upload & Convert", "2. Unify PROCESSAREA", "3. Split, Mine & Export"]

if "_page" in st.session_state:
    page = st.sidebar.radio("", PAGES, index=st.session_state["_page"])
else:
    page = st.sidebar.radio("", PAGES)

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
    st.header("Upload CSV or XES")
    uploaded = st.file_uploader("Upload a process flow CSV or XES file", type=["csv", "xes"])

    if uploaded:
        tmp = Path(tempfile.gettempdir()) / uploaded.name

        if uploaded.name.lower().endswith(".xes"):
            raw_bytes = uploaded.read()
            tmp.write_bytes(raw_bytes)
            with st.spinner("Reading XES..."):
                log = pm4py.read_xes(str(tmp))
                events = pm4py.convert_to_dataframe(log)
            st.session_state["events"] = events
            st.session_state["log"] = log
            st.session_state["summary"] = {
                "traces": events["case:concept:name"].nunique(),
                "events": len(events),
                "activities": events["concept:name"].unique().tolist(),
            }
            st.success(f"Loaded: {st.session_state['summary']['traces']} traces, {st.session_state['summary']['events']} events")
            st.dataframe(events.head(20), use_container_width=True)
        else:
            raw_bytes = uploaded.read()
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
                st.session_state["orig_area_map"] = orig_area_map

                # Only mine boundary PBBs for groups with >1 activity set
                boundary_results = {}
                groups_boundary = {}
                for (seg_idx, area), grp in split_df.groupby(["_seg_idx", "PROCESSAREA"]):
                    groups_boundary[(seg_idx, area)] = grp["case:concept:name"].unique().tolist()

                multi_act_groups = {
                    key for key, act_groups in activity_groups.items()
                    if len(act_groups) > 1
                }

                progress = st.progress(0)
                total = len(groups_boundary)
                mined = 0
                for i, ((seg_idx, area), case_ids) in enumerate(groups_boundary.items()):
                    if (seg_idx, area) not in multi_act_groups:
                        continue
                    mask = split_df["case:concept:name"].isin(case_ids)
                    sub_df = split_df[mask].copy()
                    label = f"{area}_seg{seg_idx}"
                    with st.spinner(f"Mining {label}..."):
                        result = mine_group(sub_df, orig_area_map)
                    result["sub_df"] = sub_df
                    boundary_results[label] = result
                    mined += 1
                    progress.progress((i + 1) / total)

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
                    result["sub_df"] = sub_df
                    activity_results[label] = result
                    progress.progress((total + i + 1) / (total + len(groups_activity)))

                st.session_state["activity_results"] = activity_results
                st.success(f"Done: {len(boundary_results)} boundary change PBBs, {len(activity_results)} variant based PBBs")

            # --- Display results & export ---
            has_boundary = "boundary_results" in st.session_state
            has_activity = "activity_results" in st.session_state
            has_combined = "combined_results" in st.session_state
            if has_boundary or has_activity or has_combined:
                all_results = {}
                if has_boundary:
                    for k, v in st.session_state["boundary_results"].items():
                        all_results[f"[Boundary Change] {k}"] = v
                if has_activity:
                    for k, v in st.session_state["activity_results"].items():
                        all_results[f"[Variant Based] {k}"] = v
                if has_combined:
                    for k, v in st.session_state["combined_results"].items():
                        all_results[k] = v

                pbb_labels = {}
                for i, label in enumerate(all_results, start=1):
                    pbb_labels[label] = f"PBB{i}"
                pbb_options = [f"{pbb_labels[k]} ({k})" for k in all_results]
                pbb_key_map = {f"{pbb_labels[k]} ({k})": k for k in all_results}

                # Show all PBBs in expanders
                for label, result in all_results.items():
                    pbb_name = pbb_labels[label]
                    with st.expander(f"**{pbb_name}** ({label}) — Fitness: {result['fitness']:.4f} | Precision: {result['precision']:.4f} | {result['n_sublogs']} sublogs", expanded=True):
                        if result["variants_allowed"]:
                            st.write("**Variants allowed:**")
                            for seq, count in result["variants_allowed"]:
                                st.write(f"  [{count:3d}x] {seq}")
                        if result["variants_not_allowed"]:
                            st.write("**Variants not allowed:**")
                            for seq, count in result["variants_not_allowed"]:
                                st.write(f"  [{count:3d}x] {seq}")
                        try:
                            tmp_bpmn = Path(tempfile.gettempdir()) / f"bpmn_{pbb_name}.png"
                            pm4py.save_vis_bpmn(result["bpmn"], str(tmp_bpmn))
                            st.image(str(tmp_bpmn))
                        except Exception as e:
                            st.warning(f"Could not render BPMN: {e}")
                        if st.button("Remove", key=f"del_{pbb_name}"):
                            if label.startswith("[Boundary Change] "):
                                st.session_state["boundary_results"].pop(label.removeprefix("[Boundary Change] "), None)
                            elif label.startswith("[Variant Based] "):
                                st.session_state["activity_results"].pop(label.removeprefix("[Variant Based] "), None)
                            elif label.startswith("[Combined] "):
                                st.session_state["combined_results"].pop(label, None)
                            st.rerun()

                # --- Combine PBBs ---
                st.divider()
                st.subheader("Combine PBBs")
                combine_mode = st.radio(
                    "Select combination mode",
                    ["Chain Models", "Merge & Re-mine"],
                    horizontal=True,
                )

                if combine_mode == "Chain Models":
                    with st.expander("How it works", expanded=False):
                        st.markdown(
                            "**Chains full BPMN models** end-to-end into one unified diagram. "
                            "The end event of each model is bridged to the start event of the next; "
                            "all gateways, branches and internal structure are **preserved**.\n\n"
                            "- Selection **order matters** — it defines the execution sequence.\n"
                            "- The intermediate start/end events are removed and the models are "
                            "reconnected directly.\n"
                            "- No re-mining or conformance check — it is a structural merge only."
                        )
                    ordered = st.multiselect(
                        "Select PBBs (in order)",
                        options=pbb_options,
                        key="combine_h_select",
                    )
                    if st.button("Create New PBB", key="combine_h_btn") and len(ordered) >= 2:
                        bpmns = [all_results[pbb_key_map[d]]["bpmn"] for d in ordered]
                        with st.spinner("Chaining BPMNs..."):
                            combined_bpmn = combine_horizontal(bpmns)
                        combined_name = " → ".join(
                            pbb_labels[pbb_key_map[d]] for d in ordered
                        )
                        combined_key = f"[Combined] {combined_name}"
                        combined_pbb = {
                            "bpmn": combined_bpmn,
                            "fitness": 0.0,
                            "precision": 0.0,
                            "n_sublogs": 0,
                            "n_events": 0,
                            "variants_allowed": [],
                            "variants_not_allowed": [],
                        }
                        st.session_state.setdefault("combined_results", {})[combined_key] = combined_pbb
                        st.rerun()

                else:
                    with st.expander("How it works", expanded=False):
                        st.markdown(
                            "**Merges raw event logs** from selected PBBs into one dataset "
                            "and **re-discovers** a fresh BPMN using Split Miner.\n\n"
                            "- Selection **order does not matter** — events are combined regardless.\n"
                            "- Gateways, duplicates and parallelism are **preserved** in the new model.\n"
                            "- Fitness and precision are re-evaluated on the merged log."
                        )
                    chosen = st.multiselect(
                        "Select PBBs to merge",
                        options=pbb_options,
                        key="combine_v_select",
                    )
                    if st.button("Create New PBB", key="combine_v_btn") and len(chosen) >= 2:
                        sublogs = [all_results[pbb_key_map[d]]["sub_df"] for d in chosen]
                        with st.spinner("Merging logs & re-mining..."):
                            combined_result = combine_vertical(
                                sublogs, st.session_state.get("orig_area_map")
                            )
                        combined_name = " + ".join(
                            pbb_labels[pbb_key_map[d]] for d in chosen
                        )
                        combined_key = f"[Combined] {combined_name}"
                        combined_result["sub_df"] = pd.concat(sublogs, ignore_index=True)
                        st.session_state.setdefault("combined_results", {})[combined_key] = combined_result
                        st.rerun()

                # Export all
                st.divider()
                st.subheader("Export")
                if st.button("Export All PBBs"):
                    import zipfile, io, csv

                    buf = io.BytesIO()
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                        # --- write summary.txt ---
                        lines = []
                        for label, result in all_results.items():
                            pbb_name = pbb_labels[label]
                            lines.append(f"{pbb_name}\t{label}")
                            lines.append(f"  Fitness: {result['fitness']:.4f}  Precision: {result['precision']:.4f}  Sublogs: {result['n_sublogs']}  Events: {result['n_events']}")
                            if result["variants_allowed"]:
                                lines.append("  Allowed variants:")
                                for seq, count in result["variants_allowed"]:
                                    lines.append(f"    [{count:3d}x] {seq}")
                            if result["variants_not_allowed"]:
                                lines.append("  Not-allowed variants:")
                                for seq, count in result["variants_not_allowed"]:
                                    lines.append(f"    [{count:3d}x] {seq}")
                            lines.append("")
                        zf.writestr("summary.txt", "\n".join(lines))

                        # --- write each BPMN ---
                        for label, result in all_results.items():
                            pbb_name = pbb_labels[label]
                            tmp_bpmn = Path(tempfile.gettempdir()) / f"{pbb_name}.bpmn"
                            pm4py.write_bpmn(result["bpmn"], str(tmp_bpmn))
                            zf.write(tmp_bpmn, arcname=f"{pbb_name}.bpmn")

                    st.download_button(
                        label="Download All PBBs (zip)",
                        data=buf.getvalue(),
                        file_name="PBBs.zip",
                        mime="application/zip",
                        key="dl_all_zip",
                    )
