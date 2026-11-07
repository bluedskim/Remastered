# TP-aware Hyrax limiter comparison (developer only)

Requires job 60 FLAC bundle (default: `/opt/cursor/artifacts/job-60f09377-limiter-compare/flac/`).

```bash
export JOB60_FLC_DIR=/path/to/flac
python experiments/tp_limiter_compare/run_job60_compare.py
```

Outputs under `/opt/cursor/artifacts/tp-hyrax-experiment/` (metrics JSON, WAV variants, optional loudness-matched previews).

Production web jobs **do not** set `REMASTERED_EXPERIMENT_TP_AWARE_LIMITER`.
