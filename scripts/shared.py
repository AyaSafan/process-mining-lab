import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "InductiveMiner_bi"))

import pm4py
from pm4py.util import constants as pm4py_constants

VSEP = pm4py_constants.DEFAULT_VARIANT_SEP
ACTIVITY_KEY = "concept:name"

_imbi_available = False
_inductive_miner = None
try:
    from local_pm4py.algo.discovery.inductive import algorithm as _inductive_miner
    _imbi_available = True
except ImportError:
    pass
