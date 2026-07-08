import sys
import os
import json
import tempfile
import PySimpleGUI as sg
import pm4py
from pm4py.objects.conversion.log import converter as log_converter
from pm4py.objects.conversion.process_tree import converter as pt_converter
from pm4py.objects.log.obj import EventLog, Trace, Event
from pm4py.util import constants as pm4py_constants
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import custom_precision_variants_align_etconformance as custom_ae
import save_proposals

VSEP = pm4py_constants.DEFAULT_VARIANT_SEP
ACTIVITY_KEY = "concept:name"

sg.theme("DarkGrey7")


def build_prefix_rows(result):
    rows = []
    for prefix, data in result["prefixes"].items():
        if data.get("unfit", False) or data["n_ee"] == 0:
            continue
        rows.append([
            " \u2192 ".join(prefix.split(VSEP)),
            data["count"],
            data["n_ee"],
            data["count"] * data["n_ee"],
            ", ".join(sorted(data["escaping_edges"])),
        ])
    rows.sort(key=lambda r: -r[3])
    return rows


def build_state_rows(result):
    groups = defaultdict(lambda: {"support": 0, "prefixes": []})
    for prefix, data in result["prefixes"].items():
        if data.get("unfit", False):
            continue
        state = frozenset(data["activated_transitions"])
        for ee in data["escaping_edges"]:
            key = (state, ee)
            groups[key]["support"] += data["count"]
            groups[key]["prefixes"].append(prefix)

    rows = []
    for (state, ee), info in sorted(groups.items(), key=lambda x: -x[1]["support"]):
        example = " \u2192 ".join(min(info["prefixes"], key=len).split(VSEP))
        rows.append([
            example,
            ee,
            info["support"],
            ", ".join(sorted(state)),
        ])
    return rows


def trace_to_str(trace):
    return " \u2192 ".join(ev[ACTIVITY_KEY] for ev in trace)


def str_to_trace(s):
    acts = [a.strip() for a in s.split("\u2192") if a.strip()]
    trace = Trace()
    for a in acts:
        ev = Event()
        ev[ACTIVITY_KEY] = a
        trace.append(ev)
    return trace


def export_log(log, path):
    pm4py.write_xes(log, path)


def main():
    log = None
    net = im = fm = None
    net_cached = im_cached = fm_cached = None
    process_tree = None
    result = None
    proposals = None
    desirable_traces = []
    undesirable_traces = []
    pending_idx = 0

    # --- Tab 1: Load & Discover ---
    tab1_layout = [
        [sg.Text("Event Log (.xes):"), sg.Input(key="-LOG-", size=(50, 1)),
         sg.FileBrowse(file_types=(("XES Files", "*.xes"),))],
        [sg.Button("Discover Model", key="-DISCOVER-")],
        [sg.HorizontalSeparator()],
        [sg.Text("Model Metrics", font="Any 14")],
        [sg.Text("Fitness:"), sg.Text("", key="-FIT-", size=(10, 1))],
        [sg.Text("Precision:"), sg.Text("", key="-PREC-", size=(10, 1))],
        [sg.Text("F1 Score:"), sg.Text("", key="-F1-", size=(10, 1))],
        [sg.Text("Model Size:"), sg.Text("", key="-SIZE-", size=(10, 1))],
        [sg.Text("Sum Escaping Edges:"), sg.Text("", key="-SUMEE-", size=(10, 1))],
        [sg.HorizontalSeparator()],
        [sg.Text("Discovered Petri Net:", font="Any 14")],
        [sg.Button("Open Petri Net in Browser", key="-VIEW_PETRI-")],
        [sg.Text("", key="-PETRI_STATUS-", size=(60, 1))],
    ]

    # --- Tab 2: Escaping Edges ---
    tab2_layout = [
        [sg.Text("Prefix-level Escaping Edges (sorted by Impact)", font="Any 14")],
        [sg.Multiline(key="-PREFIXES-", size=(120, 12), font=("Consolas", 10))],
        [sg.Text("State-grouped Escaping Edges (sorted by Support)", font="Any 14")],
        [sg.Multiline(key="-STATES-", size=(120, 12), font=("Consolas", 10))],
    ]

    # --- Tab 3: Proposals Review ---
    tab3_layout = [
        [sg.Text("Proposal", font="Any 16")],
        [sg.Text("Prefix:"), sg.Text("", key="-P_PREFIX-")],
        [sg.Text("Escaping Edge:"), sg.Text("", key="-P_EE-")],
        [sg.Text("Suggested Full Trace:", font="Any 12")],
        [sg.Multiline("", key="-P_TRACE-", size=(100, 4), font=("Consolas", 11))],
        [sg.Text("", key="-P_INDEX-", size=(20, 1))],
        [sg.Button("< Prev", key="-PREV-"), sg.Button("Next >", key="-NEXT-")],
        [sg.HorizontalSeparator()],
        [sg.Button("Add to Desirable Log", key="-ADD_DESIRABLE-", button_color=("white", "green"), size=(25, 1)),
         sg.Button("Add to Undesirable Log", key="-ADD_UNDESIRABLE-", button_color=("white", "red"), size=(25, 1))],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Text("Desirable Traces:", font="Any 12")],
            [sg.Listbox(values=[], key="-DESIRABLE-", size=(90, 5))],
            [sg.Text("Undesirable Traces:", font="Any 12")],
            [sg.Listbox(values=[], key="-UNDESIRABLE-", size=(90, 5))],
        ])],
        [sg.HorizontalSeparator()],
        [sg.Button("Export Desirable Log (.xes)", key="-EXPORT_DESIRABLE-"),
         sg.Button("Export Undesirable Log (.xes)", key="-EXPORT_UNDESIRABLE-")],
    ]

    layout = [
        [sg.TabGroup([[
            sg.Tab("Load & Discover", tab1_layout),
            sg.Tab("Escaping Edges", tab2_layout),
            sg.Tab("Proposals Review", tab3_layout),
        ]])],
        [sg.Exit()],
    ]

    window = sg.Window("Escaping Edges Reviewer", layout, size=(1100, 750), finalize=True)

    while True:
        event, values = window.read()

        if event in (sg.WIN_CLOSED, "Exit"):
            break

        # --- Discover ---
        if event == "-DISCOVER-":
            log_path = values["-LOG-"]
            if not log_path:
                sg.popup_error("Please select a log file first.")
                continue
            if not log_path.endswith(".xes"):
                sg.popup_error("Please select a .xes file.")
                continue

            try:
                event_log = log_converter.apply(pm4py.read_xes(log_path))
                window["-DISCOVER-"].update(disabled=True, text="Discovering...")
                window.refresh()

                log = event_log
                process_tree = pm4py.discover_process_tree_inductive(log)
                net, im, fm = pt_converter.apply(process_tree)

                log_fitness = pm4py.fitness_alignments(log, net, im, fm, multi_processing=False)[
                    "log_fitness"]
                precision = pm4py.precision_alignments(log, net, im, fm, multi_processing=False)
                denominator = log_fitness + precision
                f1 = 2 * (log_fitness * precision) / denominator if denominator != 0 else 0.0
                size = len(net.places) + len(net.transitions) + len(net.arcs)

                window["-FIT-"].update(f"{log_fitness:.4f}")
                window["-PREC-"].update(f"{precision:.4f}")
                window["-F1-"].update(f"{f1:.4f}")
                window["-SIZE-"].update(str(size))

                result = custom_ae.apply(log, net, im, fm)
                window["-SUMEE-"].update(str(result["sum_ee"]))

                window["-PETRI_STATUS-"].update("Model discovered. Click 'Open Petri Net in Browser' to view.")
                net_cached = net
                im_cached = im
                fm_cached = fm

                prefix_rows = build_prefix_rows(result)
                prefix_text = f"{'Prefix':<60} {'Sup':>4} {'EE':>4} {'Impact':>7}  Escaping Edges\n"
                prefix_text += "-" * 120 + "\n"
                for r in prefix_rows[:20]:
                    prefix_text += f"{r[0][:58]:<60} {r[1]:>4} {r[2]:>4} {r[3]:>7}  {r[4][:40]}\n"
                window["-PREFIXES-"].update(prefix_text)

                state_rows = build_state_rows(result)
                state_text = f"{'Example Prefix':<60} {'EE':<20} {'Sup':>4}  State (Activated Transitions)\n"
                state_text += "-" * 120 + "\n"
                for r in state_rows[:20]:
                    state_text += f"{r[0][:58]:<60} {r[1][:18]:<20} {r[2]:>4}  {r[3][:40]}\n"
                window["-STATES-"].update(state_text)

                proposals = save_proposals.build_proposals_data(
                    log, result, VSEP, activity_key=ACTIVITY_KEY)

                desirable_traces = []
                undesirable_traces = []
                pending_idx = 0

                if proposals:
                    _show_proposal(window, proposals, pending_idx,
                                   desirable_traces, undesirable_traces)
                else:
                    sg.popup("No escaping edges found \u2014 model is perfectly precise.")

                window["-DISCOVER-"].update(disabled=False, text="Discover Model")

            except Exception as e:
                sg.popup_error(f"Error: {e}")
                window["-DISCOVER-"].update(disabled=False, text="Discover Model")
                import traceback
                traceback.print_exc()

        # --- View Petri Net in browser ---
        if event == "-VIEW_PETRI-" and net_cached is not None:
            pm4py.view_petri_net(net_cached, im_cached, fm_cached)

        # --- Navigation ---
        if event == "-NEXT-" and proposals:
            pending_idx = min(pending_idx + 1, len(proposals) - 1)
            _show_proposal(window, proposals, pending_idx,
                           desirable_traces, undesirable_traces)

        if event == "-PREV-" and proposals:
            pending_idx = max(pending_idx - 1, 0)
            _show_proposal(window, proposals, pending_idx,
                           desirable_traces, undesirable_traces)

        # --- Add to Desirable ---
        if event == "-ADD_DESIRABLE-" and proposals:
            p = proposals[pending_idx]
            trace_str = values["-P_TRACE-"].strip()
            if trace_str:
                desirable_traces.append(trace_str)
                _show_proposal(window, proposals, min(pending_idx + 1, len(proposals) - 1),
                               desirable_traces, undesirable_traces)
                if pending_idx < len(proposals) - 1:
                    pending_idx += 1

        # --- Add to Undesirable ---
        if event == "-ADD_UNDESIRABLE-" and proposals:
            p = proposals[pending_idx]
            trace_str = values["-P_TRACE-"].strip()
            if trace_str:
                undesirable_traces.append(trace_str)
                _show_proposal(window, proposals, min(pending_idx + 1, len(proposals) - 1),
                               desirable_traces, undesirable_traces)
                if pending_idx < len(proposals) - 1:
                    pending_idx += 1

        # --- Export Desirable ---
        if event == "-EXPORT_DESIRABLE-":
            if not desirable_traces:
                sg.popup_error("No desirable traces added yet.")
                continue
            out_path = sg.popup_get_file(
                "Save Desirable Log As", save_as=True,
                file_types=(("XES Files", "*.xes"),),
                default_extension=".xes")
            if out_path:
                out_log = EventLog()
                for t in desirable_traces:
                    out_log.append(str_to_trace(t))
                export_log(out_log, out_path)
                sg.popup(f"Saved {len(desirable_traces)} traces to {out_path}")

        # --- Export Undesirable ---
        if event == "-EXPORT_UNDESIRABLE-":
            if not undesirable_traces:
                sg.popup_error("No undesirable traces added yet.")
                continue
            out_path = sg.popup_get_file(
                "Save Undesirable Log As", save_as=True,
                file_types=(("XES Files", "*.xes"),),
                default_extension=".xes")
            if out_path:
                out_log = EventLog()
                for t in undesirable_traces:
                    out_log.append(str_to_trace(t))
                export_log(out_log, out_path)
                sg.popup(f"Saved {len(undesirable_traces)} traces to {out_path}")

    window.close()


def _show_proposal(window, proposals, idx, desirable_traces, undesirable_traces):
    p = proposals[idx]
    full_trace = p["prefix_acts"] + [p["ee"]] + p.get("suggestion", [])
    window["-P_INDEX-"].update(f"{idx + 1} / {len(proposals)}")
    window["-P_PREFIX-"].update(" \u2192 ".join(p["prefix_acts"]))
    window["-P_EE-"].update(p["ee"])
    window["-P_TRACE-"].update(" \u2192 ".join(full_trace))
    window["-DESIRABLE-"].update(
        [f"{i+1}. {t}" for i, t in enumerate(desirable_traces)])
    window["-UNDESIRABLE-"].update(
        [f"{i+1}. {t}" for i, t in enumerate(undesirable_traces)])


if __name__ == "__main__":
    main()
