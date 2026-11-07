# PR80 real-track corpus (validation only)

**Delivered (3 / 10 planned):** job `60f09377`, `bfd67d05`, `c027d140`  
**Awaiting:** up to 7 additional full masters from read-only worker (not yet attached).

## Re-run after new zip arrives

1. Reconstruct PCM per job manifest (`original_pcm_sha256`, frames, s16le 44.1k stereo).
2. Write `mastered_matchering_16bit.wav` + run `apply_final_true_peak_protection` (PR80 head).
3. Independently measure WAV + recreated 192k AAC Summary dBTP (and tool peak).
4. Assert `<= pass_ceiling_dbtp(settings)` and report strict `<= -1.0`.
5. Record gain, LUFS I/LRA delta, attempts wall time, constant-gain residual.

Artifacts: `/opt/cursor/artifacts/tp-strict-ceiling-proof/`

Synthetic tests in `tests/test_true_peak_strict_*.py` are **not** counted toward the 10-track corpus.
