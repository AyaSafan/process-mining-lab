import pandas as pd
import pm4py
from pm4py.algo.discovery.split_miner import algorithm as split_miner


def mine_bpmn(sub_df: pd.DataFrame) -> object:
    return split_miner.apply(sub_df)


def evaluate_model(ev_log, bpmn_model) -> tuple[float, float]:
    net, im, fm = pm4py.convert_to_petri_net(bpmn_model)
    fitness = pm4py.fitness_token_based_replay(ev_log, net, im, fm)["log_fitness"]
    precision = pm4py.precision_token_based_replay(ev_log, net, im, fm)
    return fitness, precision


def get_variants(sub_df: pd.DataFrame) -> pd.Series:
    return (
        sub_df.sort_values("ROUTEOPERORDER")
        .groupby("case:concept:name")["concept:name"]
        .apply(tuple)
        .value_counts()
        .sort_values(ascending=False)
    )


def classify_variants(variants: pd.Series, net, im, fm) -> tuple[list, list]:
    allowed, not_allowed = [], []
    for seq, count in variants.items():
        vdf = pd.DataFrame({
            "case:concept:name": ["v0"] * len(seq),
            "concept:name": list(seq),
            "time:timestamp": pd.date_range("2024-01-01", periods=len(seq), freq="s"),
        })
        vlog = pm4py.convert_to_event_log(vdf)
        f = pm4py.fitness_token_based_replay(vlog, net, im, fm)["log_fitness"]
        (allowed if f == 1.0 else not_allowed).append((seq, int(count)))
    return allowed, not_allowed


def mine_group(
    sub_df: pd.DataFrame,
    orig_area_map: dict[str, str] | None = None,
) -> dict:
    """Mine a single group and return results dict."""
    bpmn = mine_bpmn(sub_df)
    net, im, fm = pm4py.convert_to_petri_net(bpmn)
    ev_log = pm4py.convert_to_event_log(sub_df)
    fitness, precision = evaluate_model(ev_log, bpmn)
    variants = get_variants(sub_df)
    allowed, not_allowed = classify_variants(variants, net, im, fm)

    def fmt_seq(s):
        if orig_area_map:
            return " -> ".join(f"{n} ({orig_area_map.get(n, '?')})" for n in s)
        return " -> ".join(s)

    return {
        "bpmn": bpmn,
        "fitness": fitness,
        "precision": precision,
        "n_sublogs": int(sub_df["case:concept:name"].nunique()),
        "n_events": int(len(sub_df)),
        "variants_allowed": [(fmt_seq(s), c) for s, c in allowed],
        "variants_not_allowed": [(fmt_seq(s), c) for s, c in not_allowed],
    }
