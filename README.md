# Remastered

YouTube 영상 URL에서 오디오를 내려받고, **matchering**으로 레퍼런스 트랙에 맞춰 마스터링한 뒤, 정지 이미지와 합쳐 MP4로 만들어 YouTube에 다시 올리는 개인용 CLI 도구입니다.

> **저작권 주의:** 본인이 권리를 갖지 않은 음원을 다시 업로드하면 YouTube **Content ID** 클레임, 수익 박탈, 또는 **삭제(takedown)** 가 발생할 수 있습니다. 이 도구는 **본인이 마스터링·배포 권한이 있는 콘텐츠** 또는 적법한 사용 범위 내에서만 사용하세요.

## 요구 사항

- Python 3.10 이상
- [ffmpeg](https://ffmpeg.org/) (PATH에 `ffmpeg` 명령 사용 가능)
- YouTube 업로드 시: Google Cloud OAuth **데스크톱(Installed app)** 클라이언트 JSON

## 설치

```bash
git clone <repository-url>
cd remastered
pip install -e ".[dev]"
```

설치 확인:

```bash
remastered --help
```

## Google Cloud / YouTube OAuth 설정

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트 생성
2. **YouTube Data API v3** 사용 설정
3. **사용자 인증 정보** → **OAuth 클라이언트 ID** → 유형 **데스크톱 앱**
4. 다운로드한 JSON을 예: `~/.config/remastered/client_secrets.json` 에 저장
5. 첫 업로드 시 브라우저에서 로그인하면 토큰이 `token_path`(기본 `~/.config/remastered/token.json`)에 저장됩니다

## 설정

`~/.config/remastered/config.toml` 예시:

```toml
[remastered]
work_dir = "~/.cache/remastered"
client_secrets = "~/.config/remastered/client_secrets.json"
token_path = "~/.config/remastered/token.json"
default_privacy = "private"
bit_depth = 16
preview = false
ffmpeg_path = "ffmpeg"
```

환경 변수 (`REMASTERED_` 접두사, 대문자):

| 변수 | 설명 |
|------|------|
| `REMASTERED_WORK_DIR` | 작업·임시 파일 디렉터리 |
| `REMASTERED_CLIENT_SECRETS` | OAuth client secrets JSON 경로 |
| `REMASTERED_TOKEN_PATH` | OAuth 토큰 캐시 경로 |
| `REMASTERED_DEFAULT_PRIVACY` | `private` / `unlisted` / `public` (기본 `private`) |
| `REMASTERED_BIT_DEPTH` | `16` 또는 `24` |
| `REMASTERED_PREVIEW` | `true` / `false` — matchering 미리듣기 WAV |

## 사용 예

### 1. 다운로드만

```bash
remastered download "https://www.youtube.com/watch?v=VIDEO_ID" -o ./work/download
```

### 2. 마스터링만

```bash
remastered master ./work/target.wav ./references/my_ref.wav -o ./work/master --preview
```

### 3. 비디오 렌더 (오디오 + 이미지)

```bash
remastered render ./work/master/mastered_16bit.wav -i ./cover.jpg -o ./out/video.mp4
```

### 4. 업로드만

```bash
remastered upload ./out/video.mp4 -t "내 곡 (Remastered)" --privacy private \
  --client-secrets ~/.config/remastered/client_secrets.json
```

### 5. 전체 파이프라인

```bash
remastered run "https://www.youtube.com/watch?v=VIDEO_ID" \
  -r ./references/reference.wav \
  --title "제목 (Remastered)" \
  --description "matchering 마스터 버전" \
  --privacy private
```

업로드 없이 로컬만:

```bash
remastered run "https://www.youtube.com/watch?v=VIDEO_ID" -r ./ref.wav --no-upload
```

## 프로젝트 구조

```
.
├── pyproject.toml          # 패키지·의존성·CLI 엔트리포인트
├── README.md
├── src/remastered/
│   ├── cli.py              # Click CLI (download / master / render / upload / run)
│   ├── config.py           # TOML + 환경 변수 설정
│   ├── download.py         # yt-dlp → WAV + 썸네일
│   ├── master.py           # matchering
│   ├── video.py            # ffmpeg 정지 화면 MP4
│   ├── upload.py           # YouTube Data API v3 OAuth 업로드
│   └── pipeline.py         # run 엔드투엔드
└── tests/                  # 오프라인·모킹 테스트 + 합성 톤 matchering
```

## 테스트

```bash
pip install -e ".[dev]"
pytest
```

합성 사인파 WAV로 matchering을 실제 실행하고, ffmpeg 렌더·yt-dlp·YouTube 호출은 모킹합니다.

## 라이선스

MIT (matchering 자체는 GPLv3 — 사용 시 해당 라이선스를 확인하세요.)
