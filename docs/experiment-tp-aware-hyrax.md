# Developer experiment: TP-aware Hyrax limiter

**Not production.** Enable only for local CLI / harness:

```bash
export REMASTERED_EXPERIMENT_TP_AWARE_LIMITER=1  # preserve_stereo_width path only
```

Web `follow_reference` jobs always use stock `mg.process()` unless the comparison harness is run separately.

## License

`src/remastered/experiments/tp_aware_limiter/hyrax_tp.py` is GPLv3-derived from matchering 2.0.6. See `NOTICES.md`.

## Harness

```bash
export JOB60_FLAC_DIR=/opt/cursor/artifacts/job-60f09377-limiter-compare/flac
export JOB60_EXCERPT_SECONDS=0   # full track
python experiments/tp_limiter_compare/run_job60_compare.py
```

Compares: stock vs tp_aware limiter, each with/without existing final TP guard.
