#!/usr/bin/env python3
"""Forwarding shim — re-exports the canonical finding schema + task helpers from
`shared/common.py`.

There is ONE source of truth for the finding schema, severity model, TB2 task
discovery, and the TOML reader: `shared/common.py`. This shim lets this skill's
behavioral checker keep importing `from common import ...` unchanged while sharing
that single definition across all three QC layers.

Do not edit here — edit `shared/common.py`.
"""
import importlib.util as _ilu
import os as _os

_CANON = _os.path.normpath(_os.path.join(
    _os.path.dirname(__file__), "..", "..", "..", "shared", "common.py"))
_spec = _ilu.spec_from_file_location("qc_shared_common", _CANON)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
globals().update({_k: _v for _k, _v in vars(_mod).items() if not _k.startswith("_")})
