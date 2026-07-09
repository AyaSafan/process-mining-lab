import os
import tempfile
import pm4py
from pm4py.objects.log.obj import EventLog, Trace, Event
from shared import ACTIVITY_KEY


def _trace_from_string(s):
    acts = [a.strip() for a in s.split("\u2192") if a.strip()]
    trace = Trace()
    for a in acts:
        ev = Event()
        ev[ACTIVITY_KEY] = a
        trace.append(ev)
    return trace


def traces_to_log(strings):
    out = EventLog()
    for s in strings:
        out.append(_trace_from_string(s))
    return out


def export_traces(strings, filename):
    out = traces_to_log(strings)
    path = os.path.join(tempfile.gettempdir(), filename)
    pm4py.write_xes(out, path)
    return path


def export_desirable(state):
    if state is None or not state["desirable"]:
        raise ValueError("No desirable traces to export.")
    return export_traces(state["desirable"], "desirable_log.xes")


def export_undesirable(state):
    if state is None or not state["undesirable"]:
        raise ValueError("No undesirable traces to export.")
    return export_traces(state["undesirable"], "undesirable_log.xes")


def build_undesirable_log(state):
    return traces_to_log(state["undesirable"])


def format_trace_list(strings):
    if not strings:
        return "(empty)"
    return "\n".join(f"{i+1}. {t}" for i, t in enumerate(strings))
