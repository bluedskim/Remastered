# Developer experiment: TP-aware Hyrax limiter

**Not production.** Default web pipeline is unchanged.

## Enable (CLI `preserve_stereo_width` path only)

```bash
export REMASTERED_EXPERIMENT_TP_AWARE_LIMITER=1
```

## Harness (job 60 retained FLACs)

```bash
export JOB60_FLAC_DIR=/opt/cursor/artifacts/job-60f09377-limiter-compare/flac
export JOB60_EXCERPT_SECONDS=0
python experiments/tp_limiter_compare/run_job60_compare.py
```

Metrics JSON: `/opt/cursor/artifacts/tp-hyrax-experiment/metrics_job60.json`

## Technical notes (corrected)

- **Limiter gain reduction** reshapes dynamics; that is not undone by later constant attenuation.
- **Inter-sample peaks** can often be brought under a TP ceiling with **gain only**, without a limiter; the limiter’s envelope is a separate effect from ISP.
- Experimental `hyrax_tp` uses **4× oversampled Hyrax gain** and **conservative gain downsampling** — no `np.clip` hard ceiling.
- **Production TP guard** may pass with AAC up to `ceiling + headroom` (−0.5 dBTP at default settings) while **strict −1.0 dBTP** on decoded AAC still fails — report both.

## License

`hyrax_tp.py` is GPLv3-derived from matchering 2.0.6 (`NOTICES.md`).
