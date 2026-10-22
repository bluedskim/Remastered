from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import matchering as mg
import numpy as np
from matchering.dsp import amplify
from matchering.limiter import limit

from remastered.config import BitDepth
from remastered.paths import ensure_dir
from remastered.stereo_width import (
    DEFAULT_STEREO_WIDTH_MODE,
    StereoWidthMode,
    preserve_stereo_width,
    stereo_side_mid_ratio_db,
)

logger = logging.getLogger(__name__)


@dataclass
class MasterResult:
    output_path: Path
    preview_path: Path | None


def _matchering_no_limiter(
    target: np.ndarray,
    reference: np.ndarray,
    config: mg.Config,
) -> tuple[np.ndarray, float]:
    from matchering.stages import (
        __correct_levels,
        __match_frequencies,
        __match_levels,
    )

    (
        target_mid,
        target_side,
        final_amplitude_coefficient,
        target_mid_loudest_pieces,
        target_side_loudest_pieces,
        reference_mid_loudest_pieces,
        reference_side_loudest_pieces,
        target_divisions,
        target_piece_size,
        reference_match_rms,
    ) = __match_levels(target, reference, config)

    result_no_limiter, result_no_limiter_mid = __match_frequencies(
        target_mid,
        target_side,
        target_mid_loudest_pieces,
        reference_mid_loudest_pieces,
        target_side_loudest_pieces,
        reference_side_loudest_pieces,
        config,
    )

    result_no_limiter = __correct_levels(
        result_no_limiter,
        result_no_limiter_mid,
        target_divisions,
        target_piece_size,
        reference_match_rms,
        config,
    )
    return result_no_limiter, final_amplitude_coefficient


def _load_and_check_pair(
    target_wav: Path,
    reference_wav: Path,
    config: mg.Config,
) -> tuple[np.ndarray, np.ndarray, int]:
    from matchering.checker import check, check_equality
    from matchering.loader import load

    temp_folder = config.temp_folder or str(Path(target_wav).parent)
    target, target_sr = load(str(target_wav), "target", temp_folder)
    target, target_sr = check(target, target_sr, config, "target")
    reference, reference_sr = load(str(reference_wav), "reference", temp_folder)
    reference, reference_sr = check(reference, reference_sr, config, "reference")
    if not config.allow_equality:
        check_equality(target, reference)
    if (
        not (target_sr == reference_sr == config.internal_sample_rate)
        or target.shape[1] != 2
        or reference.shape[1] != 2
    ):
        from matchering.log import Code, ModuleError

        raise ModuleError(Code.ERROR_VALIDATION)
    return target, reference, int(config.internal_sample_rate)


def _finalize_limited(
    audio: np.ndarray,
    final_amplitude_coefficient: float,
    config: mg.Config,
) -> np.ndarray:
    limited = limit(audio, config)
    return amplify(limited, final_amplitude_coefficient)


def run_matchering(
    target_wav: Path,
    reference_wav: Path,
    out_dir: Path,
    *,
    bit_depth: BitDepth = 16,
    preview: bool = False,
    stem: str = "mastered",
    stereo_width: StereoWidthMode = DEFAULT_STEREO_WIDTH_MODE,
    log_line: Callable[[str], None] | None = None,
) -> MasterResult:
    """matchering으로 target을 reference에 맞춰 마스터링합니다."""
    ensure_dir(out_dir)
    if not target_wav.is_file():
        raise FileNotFoundError(f"Target file not found: {target_wav}")
    if not reference_wav.is_file():
        raise FileNotFoundError(f"Reference file not found: {reference_wav}")

    def _log(msg: str) -> None:
        if log_line:
            log_line(msg)
        logger.info(msg)

    suffix = "16" if bit_depth == 16 else "24"
    output_path = out_dir / f"{stem}_{suffix}bit.wav"
    subtype = "PCM_16" if bit_depth == 16 else "PCM_24"
    preview_path: Path | None = None
    preview_result: mg.Result | None = None
    if preview:
        preview_path = out_dir / f"{stem}_preview.wav"
        preview_result = mg.pcm16(str(preview_path))

    config = mg.Config()
    config.temp_folder = str(out_dir / ".matchering_tmp")
    ensure_dir(Path(config.temp_folder))

    logger.info(
        "matchering 시작: target=%s reference=%s bit=%s stereo_width=%s",
        target_wav,
        reference_wav,
        bit_depth,
        stereo_width,
    )

    if stereo_width == "follow_reference":
        results: list[mg.Result] = [mg.Result(str(output_path), subtype)]
        mg.process(
            str(target_wav),
            str(reference_wav),
            results,
            config=config,
            preview_result=preview_result,
        )
    else:
        target, reference, sample_rate = _load_and_check_pair(
            target_wav, reference_wav, config
        )
        target_snapshot = np.copy(target)
        sm_orig = stereo_side_mid_ratio_db(target_snapshot)
        _log(f"Stereo width (original Side/Mid): {sm_orig:.2f} dB")

        pre_limiter, final_amp = _matchering_no_limiter(target, reference, config)
        sm_pre = stereo_side_mid_ratio_db(pre_limiter)
        _log(f"Stereo width (after mastering, before correction): {sm_pre:.2f} dB")

        corrected = preserve_stereo_width(target_snapshot, pre_limiter, sample_rate)
        sm_corr = stereo_side_mid_ratio_db(corrected)
        _log(f"Stereo width (after width correction): {sm_corr:.2f} dB")

        final = _finalize_limited(corrected, final_amp, config)
        sm_final = stereo_side_mid_ratio_db(final)
        _log(f"Stereo width (after limiter): {sm_final:.2f} dB")

        from matchering.saver import save

        save(str(output_path), final, sample_rate, subtype)
        if preview_result is not None:
            from matchering.preview_creator import create_preview

            create_preview(target_snapshot, final, config, None, preview_result)

    if not output_path.is_file():
        raise RuntimeError("matchering did not produce an output file")

    logger.info("마스터링 완료: %s", output_path)
    return MasterResult(output_path=output_path, preview_path=preview_path if preview else None)
