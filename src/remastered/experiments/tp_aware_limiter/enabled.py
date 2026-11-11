from __future__ import annotations

import os


def experiment_tp_aware_limiter_enabled() -> bool:
    return os.environ.get("REMASTERED_EXPERIMENT_TP_AWARE_LIMITER", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
