from collections import defaultdict
from shared import VSEP


def build_prefix_rows(result):
    rows = []
    for prefix, data in result["prefixes"].items():
        if data.get("unfit", False) or data["n_ee"] == 0:
            continue
        rows.append({
            "Prefix": " \u2192 ".join(prefix.split(VSEP)),
            "Support": data["count"],
            "|EE|": data["n_ee"],
            "Impact": data["count"] * data["n_ee"],
            "Escaping Edges": ", ".join(sorted(data["escaping_edges"])),
        })
    rows.sort(key=lambda r: -r["Impact"])
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
        rows.append({
            "Example Prefix": example,
            "EE": ee,
            "Support": info["support"],
            "State (Activated Transitions)": ", ".join(sorted(state)),
        })
    return rows



