from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import matchering as mg

from remastered.config import BitDepth
from remastered.paths import ensure_dir

logger = logging.getLogger(__name__)


@dataclass
class MasterResult:
    output_path: Path
    preview_path: Path | None


def run_matchering(
    target_wav: Path,
    reference_wav: Path,
    out_dir: Path,
    *,
    bit_depth: BitDepth = 16,
    preview: bool = False,
    stem: str = "mastered",
) -> MasterResult:
    """matchering으로 target을 reference에 맞춰 마스터링합니다."""
    ensure_dir(out_dir)
    if not target_wav.is_file():
        raise FileNotFoundError(f"타깃 파일 없음: {target_wav}")
    if not reference_wav.is_file():
        raise FileNotFoundError(f"레퍼런스 파일 없음: {reference_wav}")

    suffix = "16" if bit_depth == 16 else "24"
    output_path = out_dir / f"{stem}_{suffix}bit.wav"
    results: list[mg.Result] = []
    if bit_depth == 16:
        results.append(mg.pcm16(str(output_path)))
    else:
        results.append(mg.pcm24(str(output_path)))

    preview_path: Path | None = None
    preview_result: mg.Result | None = None
    if preview:
        preview_path = out_dir / f"{stem}_preview.wav"
        preview_result = mg.pcm16(str(preview_path))

    config = mg.Config()
    config.temp_folder = str(out_dir / ".matchering_tmp")
    ensure_dir(Path(config.temp_folder))

    logger.info(
        "matchering 시작: target=%s reference=%s bit=%s",
        target_wav,
        reference_wav,
        bit_depth,
    )
    mg.process(
        str(target_wav),
        str(reference_wav),
        results,
        config=config,
        preview_result=preview_result,
    )
    if not output_path.is_file():
        raise RuntimeError("matchering이 출력 파일을 생성하지 않았습니다")

    logger.info("마스터링 완료: %s", output_path)
    return MasterResult(output_path=output_path, preview_path=preview_path if preview else None)
