import json
from collections import defaultdict, Counter


def _most_common_suffix_after(ee, event_log, activity_key="concept:name"):
    suffixes = []
    for trace in event_log:
        acts = [ev[activity_key] for ev in trace]
        try:
            pos = acts.index(ee)
            suffixes.append(tuple(acts[pos + 1:]))
        except ValueError:
            continue
    if not suffixes:
        return []
    best = Counter(suffixes).most_common(1)[0][0]
    return list(best)


def build_proposals_data(event_log, result, VSEP, activity_key="concept:name"):
    trace_strs = [
        VSEP.join(ev[activity_key] for ev in trace)
        for trace in event_log
    ]

    groups = defaultdict(lambda: {"support": 0, "prefixes": []})
    for prefix, data in result["prefixes"].items():
        if data.get("unfit", False):
            continue
        state = frozenset(data["activated_transitions"])
        for ee in data["escaping_edges"]:
            key = (state, ee)
            groups[key]["support"] += data["count"]
            groups[key]["prefixes"].append(prefix)

    proposals = []
    for (state, ee), info in sorted(groups.items(),
                                     key=lambda x: -x[1]["support"]):
        example = min(info["prefixes"], key=len)
        prefix_acts = example.split(VSEP)

        suggestion = _most_common_suffix_after(ee, event_log, activity_key)

        proposals.append({
            "state": sorted(state),
            "ee": ee,
            "example": example,
            "prefix_acts": prefix_acts,
            "suggestion": suggestion,
            "support": info["support"],
        })

    return proposals


def save_proposals_json(event_log, result, VSEP,
                         output_path, activity_key="concept:name"):
    proposals = build_proposals_data(
        event_log, result, VSEP, activity_key=activity_key)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(proposals, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(proposals)} proposals to {output_path}")
    return proposals
