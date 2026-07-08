"""
Expert review CLI for escaping edge proposals.

Usage:
    python scripts/review_proposals.py proposals.json output_dir

Saves augmented_traces.xes (allowed) and undesired_traces.xes (disallowed).
"""
import json
import sys
import os
from pm4py.objects.log.obj import EventLog, Trace, Event
import pm4py


def load_proposals(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_trace(prefix_acts, escaping_edge, continuation,
                activity_key="concept:name"):
    new = Trace()
    for act in prefix_acts:
        new.append(Event({activity_key: act}))
    new.append(Event({activity_key: escaping_edge}))
    for act in (continuation or []):
        new.append(Event({activity_key: act}))
    return new


def review(proposals, output_dir, activity_key="concept:name"):
    os.makedirs(output_dir, exist_ok=True)

    allowed = []
    disallowed = []
    skipped = []

    for i, p in enumerate(proposals):
        prefix_disp = " \u2192 ".join(p["prefix_acts"])
        full_disp = " \u2192 ".join(
            p["prefix_acts"] + [p["ee"]] + p["suggestion"])
        suffix_disp = " \u2192 ".join(p["suggestion"]) or "(empty)"

        print("=" * 70)
        print(f"[{i+1}/{len(proposals)}]")
        print()
        print(f"Prefix:            {prefix_disp}")
        print(f"Escaping Edge:     {p['ee']}")
        print(f"Suggested suffix:  {suffix_disp}")
        print(f"Suggested full trace:")
        print(f"  {full_disp}")
        print("-" * 70)
        print()

        while True:
            print("  a  = allow (save to augmented_traces.xes)")
            print("  d  = disallow (save to undesired_traces.xes)")
            print("  e  = edit continuation (type comma-separated activities)")
            print("  s  = skip")
            choice = input("Action: ").strip().lower()

            if choice == "a":
                trace = build_trace(
                    p["prefix_acts"], p["ee"], p["suggestion"],
                    activity_key=activity_key)
                allowed.append(trace)
                break

            elif choice == "d":
                trace = build_trace(
                    p["prefix_acts"], p["ee"], p["suggestion"],
                    activity_key=activity_key)
                disallowed.append(trace)
                break

            elif choice == "e":
                custom = input("  Continuation (comma-separated): ").strip()
                cont = [x.strip() for x in custom.split(",") if x.strip()]
                which = input("  Save to (a)llowed or (d)isallowed? [a]: ").strip().lower()
                trace = build_trace(
                    p["prefix_acts"], p["ee"], cont,
                    activity_key=activity_key)
                if which == "d":
                    disallowed.append(trace)
                else:
                    allowed.append(trace)
                break

            elif choice == "s":
                skipped.append(i)
                break

            else:
                print("  Invalid.\n")

    allow_path = os.path.join(output_dir, "augmented_traces.xes")
    disallow_path = os.path.join(output_dir, "undesired_traces.xes")

    if allowed:
        pm4py.write_xes(EventLog(allowed), allow_path)
        print(f"\nSaved {len(allowed)} allowed trace(s) to {allow_path}")
    if disallowed:
        pm4py.write_xes(EventLog(disallowed), disallow_path)
        print(f"Saved {len(disallowed)} undesired trace(s) to {disallow_path}")
    if skipped:
        print(f"Skipped: {len(skipped)}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    proposals = load_proposals(sys.argv[1])
    review(proposals, sys.argv[2])
