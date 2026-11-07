# True-peak-aware Hyrax experiment

`hyrax_tp.py` is derived from **matchering 2.0.6** (`matchering/limiter/hyrax.py`, Copyright Sergree),
distributed under **GNU GPL v3**. Remastered is MIT-licensed; this experimental module is GPL v3
when distributed as part of a combined work. See upstream: https://github.com/sergree/matchering

Changes: oversampled peak detection wrapper around the stock Hyrax envelope (same attack/hold/release).

**Not enabled in production** unless `REMASTERED_EXPERIMENT_TP_AWARE_LIMITER=1` (developer/CLI only).
