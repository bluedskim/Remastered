from __future__ import annotations

import logging
from pathlib import Path

import click

from remastered import __version__
from remastered.config import resolve_settings
from remastered.download import download_audio
from remastered.logging_setup import setup_logging
from remastered.master import run_matchering
from remastered.paths import ensure_dir
from remastered.pipeline import run_pipeline
from remastered.upload import upload_video
from remastered.video import generate_placeholder_image, render_still_video

logger = logging.getLogger(__name__)

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


def _settings_from_ctx(ctx: click.Context) -> tuple:
    cfg_path = ctx.obj.get("config") if ctx.obj else None
    verbose = ctx.obj.get("verbose", False) if ctx.obj else False
    overrides = ctx.obj.get("overrides", {}) if ctx.obj else {}
    settings = resolve_settings(Path(cfg_path) if cfg_path else None, overrides)
    return settings, verbose


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(__version__, prog_name="remastered")
@click.option(
    "--config",
    "-c",
    type=click.Path(path_type=Path, exists=False),
    help="설정 TOML 파일 경로 (기본: ~/.config/remastered/config.toml)",
)
@click.option("--verbose", "-v", is_flag=True, help="자세한 로그 출력")
@click.pass_context
def main(ctx: click.Context, config: Path | None, verbose: bool) -> None:
    """YouTube 오디오 다운로드, matchering 마스터링, YouTube 재업로드 도구."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = str(config) if config else None
    ctx.obj["verbose"] = verbose
    ctx.obj["overrides"] = {}


@main.command("download")
@click.argument("url")
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path),
    help="WAV·썸네일 저장 디렉터리 (기본: work_dir/download)",
)
@click.pass_context
def cmd_download(ctx: click.Context, url: str, output_dir: Path | None) -> None:
    """YouTube URL에서 오디오(WAV)와 썸네일을 내려받습니다."""
    settings, verbose = _settings_from_ctx(ctx)
    setup_logging(verbose)
    out = output_dir or ensure_dir(settings.work_dir / "download")
    result = download_audio(url, out, yt_dlp_path=settings.yt_dlp_path)
    click.echo(f"오디오: {result.audio_path}")
    if result.thumbnail_path:
        click.echo(f"썸네일: {result.thumbnail_path}")
    click.echo(f"제목: {result.title}")


@main.command("master")
@click.argument("target", type=click.Path(path_type=Path, exists=True))
@click.argument("reference", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(path_type=Path),
    help="마스터 WAV 출력 디렉터리",
)
@click.option("--bit-depth", type=click.Choice(["16", "24"]), default=None)
@click.option("--preview", is_flag=True, help="미리듣기 WAV도 생성")
@click.pass_context
def cmd_master(
    ctx: click.Context,
    target: Path,
    reference: Path,
    output_dir: Path | None,
    bit_depth: str | None,
    preview: bool,
) -> None:
    """타깃 WAV를 레퍼런스에 맞춰 matchering으로 마스터링합니다."""
    settings, verbose = _settings_from_ctx(ctx)
    setup_logging(verbose)
    out = output_dir or ensure_dir(settings.work_dir / "master")
    depth = int(bit_depth) if bit_depth else settings.bit_depth
    result = run_matchering(
        target,
        reference,
        out,
        bit_depth=depth,
        preview=preview or settings.preview,
    )
    click.echo(f"마스터 결과: {result.output_path}")
    if result.preview_path:
        click.echo(f"미리듣기: {result.preview_path}")


@main.command("render")
@click.argument("audio", type=click.Path(path_type=Path, exists=True))
@click.option(
    "--image",
    "-i",
    type=click.Path(path_type=Path, exists=True),
    help="커버 이미지 (없으면 플레이스홀더 생성)",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="출력 MP4 경로",
)
@click.pass_context
def cmd_render(
    ctx: click.Context,
    audio: Path,
    image: Path | None,
    output: Path,
) -> None:
    """마스터 오디오와 정지 이미지로 MP4를 렌더합니다."""
    settings, verbose = _settings_from_ctx(ctx)
    setup_logging(verbose)
    cover = image
    if cover is None:
        cover = generate_placeholder_image(output.parent / "cover.jpg")
    path = render_still_video(audio, cover, output, ffmpeg_path=settings.ffmpeg_path)
    click.echo(f"비디오: {path}")


@main.command("upload")
@click.argument("video", type=click.Path(path_type=Path, exists=True))
@click.option("--title", "-t", required=True, help="YouTube 제목")
@click.option("--description", "-d", default="", help="설명")
@click.option(
    "--privacy",
    type=click.Choice(["private", "unlisted", "public"]),
    default=None,
    help="공개 설정 (기본: private)",
)
@click.option(
    "--client-secrets",
    type=click.Path(path_type=Path, exists=True),
    help="Google OAuth client_secrets.json",
)
@click.pass_context
def cmd_upload(
    ctx: click.Context,
    video: Path,
    title: str,
    description: str,
    privacy: str | None,
    client_secrets: Path | None,
) -> None:
    """MP4를 YouTube에 업로드합니다 (OAuth 필요)."""
    settings, verbose = _settings_from_ctx(ctx)
    setup_logging(verbose)
    secrets = client_secrets or settings.client_secrets
    if not secrets:
        raise click.UsageError(
            "--client-secrets 또는 설정/환경 변수 REMASTERED_CLIENT_SECRETS가 필요합니다."
        )
    vid = upload_video(
        video,
        client_secrets=secrets,
        token_path=settings.token_path,
        title=title,
        description=description,
        privacy=privacy or settings.default_privacy,
        category_id=settings.upload_category_id,
    )
    click.echo(f"업로드 완료: https://www.youtube.com/watch?v={vid}")


@main.command("run")
@click.argument("url")
@click.option(
    "--reference",
    "-r",
    type=click.Path(path_type=Path, exists=True),
    required=True,
    help="마스터링 레퍼런스 WAV",
)
@click.option("--title", "-t", default=None, help="업로드 제목 (기본: 원본 제목 + Remastered)")
@click.option("--description", "-d", default="", help="업로드 설명")
@click.option(
    "--privacy",
    type=click.Choice(["private", "unlisted", "public"]),
    default=None,
)
@click.option("--cover", type=click.Path(path_type=Path, exists=True), help="커버 이미지")
@click.option("--no-upload", is_flag=True, help="마스터·렌더만 하고 업로드 생략")
@click.option(
    "--client-secrets",
    type=click.Path(path_type=Path, exists=True),
    help="Google OAuth client_secrets.json",
)
@click.pass_context
def cmd_run(
    ctx: click.Context,
    url: str,
    reference: Path,
    title: str | None,
    description: str,
    privacy: str | None,
    cover: Path | None,
    no_upload: bool,
    client_secrets: Path | None,
) -> None:
    """다운로드 → 마스터링 → 비디오 렌더 → (선택) YouTube 업로드를 한 번에 실행합니다."""
    settings, verbose = _settings_from_ctx(ctx)
    if client_secrets:
        settings = settings.model_copy(update={"client_secrets": client_secrets})
    setup_logging(verbose)
    paths = run_pipeline(
        url,
        reference,
        settings,
        title=title,
        description=description,
        privacy=privacy,
        cover_image=cover,
        skip_upload=no_upload,
    )
    for key, value in paths.items():
        click.echo(f"{key}: {value}")


if __name__ == "__main__":
    main()
