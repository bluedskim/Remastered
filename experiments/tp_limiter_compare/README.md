# TP-aware Hyrax limiter comparison (developer only)

Requires job 60 FLAC bundle (default: `/opt/cursor/artifacts/job-60f09377-limiter-compare/flac/`).

```bash
export JOB60_FLAC_DIR=/opt/cursor/artifacts/job-60f09377-limiter-compare/flac
# Large full-track outputs: use local ext4 (artifact FS may reject large os.replace)
export TP_HYRAX_OUT=/tmp/tp-hyrax-experiment
python experiments/tp_limiter_compare/run_job60_compare.py
cp /tmp/tp-hyrax-experiment/metrics_job60.json /opt/cursor/artifacts/tp-hyrax-experiment/
```

Outputs under `/opt/cursor/artifacts/tp-hyrax-experiment/` (metrics JSON, WAV variants, optional loudness-matched previews).

Production web jobs **do not** set `REMASTERED_EXPERIMENT_TP_AWARE_LIMITER`.
