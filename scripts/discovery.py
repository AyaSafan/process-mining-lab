import os
import tempfile
import pandas as pd
import pm4py
from pm4py.objects.conversion.log import converter as log_converter
from pm4py.objects.log.obj import EventLog
from shared import _imbi_available, _inductive_miner, ACTIVITY_KEY, VSEP
import custom_precision_variants_align_etconformance as custom_ae
import save_proposals
from proposals import build_prefix_rows, build_state_rows
from log_utils import build_undesirable_log, traces_to_log


def discover(log_path, noise_threshold, progress=None):
    if not log_path or not log_path.endswith(".xes"):
        raise ValueError("Please select a .xes file.")

    if progress:
        progress(0.1, "Loading log...")
    event_log = log_converter.apply(pm4py.read_xes(log_path))

    if not _imbi_available:
        raise RuntimeError("Inductive Miner - bi is not available.")

    if progress:
        progress(0.3, f"Discovering with IMbi (sup={noise_threshold})...")
    empty_log = EventLog()
    net, im, fm = _inductive_miner.apply_bi(
        event_log, empty_log,
        variant=_inductive_miner.Variants.IMbi,
        sup=noise_threshold,
        ratio=0,
        size_par=0,
    )

    if progress:
        progress(0.6, "Computing metrics...")
    log_fitness = pm4py.fitness_alignments(event_log, net, im, fm, multi_processing=False)["log_fitness"]
    precision = pm4py.precision_alignments(event_log, net, im, fm, multi_processing=False)
    denominator = log_fitness + precision
    f1 = 2 * (log_fitness * precision) / denominator if denominator != 0 else 0.0
    size = len(net.places) + len(net.transitions) + len(net.arcs)

    if progress:
        progress(0.8, "Computing escaping edges...")
    result = custom_ae.apply(event_log, net, im, fm)

    if progress:
        progress(0.9, "Building proposals...")
    proposals = save_proposals.build_proposals_data(event_log, result, VSEP, activity_key=ACTIVITY_KEY)

    img_path = os.path.join(tempfile.gettempdir(), "petri_net_preview.png")
    pm4py.save_vis_petri_net(net, im, fm, img_path)

    prefix_rows = build_prefix_rows(result)
    state_rows = build_state_rows(result)
    prefix_df = pd.DataFrame(prefix_rows) if prefix_rows else pd.DataFrame(
        columns=["Prefix", "Support", "|EE|", "Impact", "Escaping Edges"])
    state_df = pd.DataFrame(state_rows) if state_rows else pd.DataFrame(
        columns=["Example Prefix", "EE", "Support", "State (Activated Transitions)"])

    app_state = {
        "log": event_log,
        "net": net,
        "im": im,
        "fm": fm,
        "result": result,
        "proposals": proposals,
        "desirable": [],
        "undesirable": [],
        "idx": 0,
    }

    metrics = (
        f"Fitness: {log_fitness:.4f}\n"
        f"Precision: {precision:.4f}\n"
        f"F1 Score: {f1:.4f}\n"
        f"Model Size: {size}\n"
        f"Sum Escaping Edges: {result['sum_ee']}"
    )

    return img_path, metrics, prefix_df, state_df, app_state


def rediscover_imbi(state, noise_threshold, ratio, progress=None):
    if state is None:
        raise ValueError("Discover a model first.")

    logP = state["log"]
    logM = build_undesirable_log(state)

    if len(logM) == 0:
        raise ValueError("No undesirable traces collected.")

    des_log = traces_to_log(state["desirable"])
    for t in logP:
        des_log.append(t)

    size_par = len(des_log) / len(logM) if len(logM) > 0 else 1.0

    if not _imbi_available:
        raise RuntimeError("Inductive Miner - bi is not available.")

    if progress:
        progress(0.2, "Running Inductive Miner - bi...")
    net, im, fm = _inductive_miner.apply_bi(
        des_log, logM,
        variant=_inductive_miner.Variants.IMbi,
        sup=noise_threshold,
        ratio=ratio,
        size_par=size_par,
    )

    if progress:
        progress(0.6, "Computing metrics...")
    log_fitness = pm4py.fitness_alignments(des_log, net, im, fm, multi_processing=False)["log_fitness"]
    precision = pm4py.precision_alignments(des_log, net, im, fm, multi_processing=False)
    denominator = log_fitness + precision
    f1 = 2 * (log_fitness * precision) / denominator if denominator != 0 else 0.0
    size = len(net.places) + len(net.transitions) + len(net.arcs)

    if progress:
        progress(0.8, "Rendering Petri net...")
    img_path = os.path.join(tempfile.gettempdir(), "petri_net_imbi_preview.png")
    pm4py.save_vis_petri_net(net, im, fm, img_path)

    metrics = (
        f"Fitness: {log_fitness:.4f}\n"
        f"Precision: {precision:.4f}\n"
        f"F1 Score: {f1:.4f}\n"
        f"Model Size: {size}\n"
        f"Original traces: {len(state['log'])} | Desirable added: {len(state['desirable'])} | Undesirable: {len(logM)} / {len(state['undesirable'])}"
    )

    return img_path, metrics
