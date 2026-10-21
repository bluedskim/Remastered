# Remastered

YouTube 영상 URL에서 오디오를 내려받고, **matchering**으로 레퍼런스 트랙에 맞춰 마스터링한 뒤, 정지 이미지와 합쳐 MP4로 만들어 YouTube에 다시 올리는 개인용 CLI 도구입니다.

> **저작권 주의:** 본인이 권리를 갖지 않은 음원을 다시 업로드하면 YouTube **Content ID** 클레임, 수익 박탈, 또는 **삭제(takedown)** 가 발생할 수 있습니다. 이 도구는 **본인이 마스터링·배포 권한이 있는 콘텐츠** 또는 적법한 사용 범위 내에서만 사용하세요.

## 요구 사항

- Python 3.10 이상 (로컬 설치) **또는** Docker (Apple Silicon M2 등)
- [ffmpeg](https://ffmpeg.org/) (PATH에 `ffmpeg` 명령 사용 가능 — Docker 이미지에 포함)
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
| `REMASTERED_CONFIG` | 설정 TOML 경로 (Docker에서 `/config/config.toml` 등) |
| `REMASTERED_OAUTH_LOCAL_PORT` | Docker·헤드리스 환경 OAuth 로컬 서버 포트 (예: `8095`) |
| `REMASTERED_OAUTH_USE_CONSOLE` | `true`면 URL·코드 복사·붙여넣기 OAuth (`remastered auth --console`) |
| `REMASTERED_WEB_PUBLIC_BASE_URL` | 공개 URL (예: `https://remastered.baem.app`) — OAuth 리다이렉트·프록시용 |
| `REMASTERED_GOOGLE_CLIENT_ID` / `SECRET` | 웹 OAuth 클라이언트 (또는 `web` 유형 JSON) |
| `REMASTERED_TOKEN_ENCRYPTION_KEY` | 사용자 refresh 토큰 암호화 Fernet 키 (필수) |
| `REMASTERED_WEB_SESSION_SECRET` | 세션 쿠키 서명 |
| `REMASTERED_WEB_DAILY_JOB_QUOTA_PER_USER` | 사용자당 일일 작업 수 (기본 5) |
| `REMASTERED_WEB_MAX_SOURCE_DURATION_SECONDS` | 소스 최대 길이(초) |
| `REMASTERED_WEB_JOB_RETENTION_DAYS` | 완료 작업 파일 보존 일수 |

## Docker (Apple Silicon M2 / linux/arm64)

이미지는 **linux/arm64**와 **linux/amd64** 모두에서 빌드·실행할 수 있습니다. M2 Mac에서는 Docker Desktop이 **linux/arm64** 네이티브로 동작합니다.

### 웹 UI (공개 멀티유저 — remastered.baem.app)

방문자는 **Google 로그인** 후 YouTube URL을 **다운로드·마스터링**합니다(웹에서는 MP4 렌더/YouTube 업로드 없음). OAuth refresh 토큰은 SQLite `users` 테이블에 **암호화 저장**되며, 작업·파일은 **사용자별로 격리**됩니다. 익명 사용자는 랜딩·로그인만 가능합니다.

```bash
# Fernet 키 생성
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

export REMASTERED_WEB_PUBLIC_BASE_URL=https://remastered.baem.app
export REMASTERED_GOOGLE_CLIENT_ID=...
export REMASTERED_GOOGLE_CLIENT_SECRET=...
export REMASTERED_TOKEN_ENCRYPTION_KEY=...
export REMASTERED_WEB_SESSION_SECRET=...

docker compose build web
docker compose up -d web
```

Cloudflare Tunnel 등 리버스 프록시는 `X-Forwarded-Proto: https`를 전달해야 합니다 (앱에 ProxyHeaders 미들웨어 포함).

**Google OAuth:** 웹 로그인은 `openid` + email + profile만 요청합니다(민감 범위 검증 불필요). CLI 업로드는 별도 데스크톱 OAuth를 사용합니다.

**한도 (기본):** 사용자당 동시 1건 · 일 5건 · 소스 최대 15분 · 전역 작업 큐 1 워커 · 오래된 파일 자동 정리.

CLI(로컬·개인)는 기존처럼 `token.json` + `docker compose run --rm remastered auth`를 사용합니다.

#### Google Cloud Console (Mac mini / 공개 웹)

1. **API 및 서비스** → **YouTube Data API v3** 사용 설정  
2. **사용자 인증 정보** → **OAuth 클라이언트 ID** → 유형 **웹 애플리케이션**  
3. **승인된 리디렉션 URI:** `https://remastered.baem.app/oauth/callback` (로컬 테스트 시 `http://localhost:8080/oauth/callback` 추가)  
4. **OAuth 동의 화면:** `openid`, `email`, `profile` (웹 로그인용)  
5. 클라이언트 ID/비밀을 `REMASTERED_GOOGLE_CLIENT_ID` / `REMASTERED_GOOGLE_CLIENT_SECRET`에 설정 (또는 `web` 키가 있는 JSON을 `client_secrets.json`으로 마운트)

### 준비 (M2 Mac)

```bash
git clone https://github.com/bluedskim/Remastered.git
cd Remastered

# 설정·비밀 디렉터리
cp docker/config.example.toml docker/config/config.toml
# Google Cloud에서 받은 JSON을 아래 경로에 두세요 (이미지에 넣지 마세요)
#   docker/config/client_secrets.json

# 레퍼런스 WAV
mkdir -p references
# cp ~/Music/my_reference.wav references/

docker compose build
```

### OAuth (컨테이너 안에서 첫 로그인)

토큰은 **`docker/config/token.json`** 에 저장되며, `docker/config` 볼륨에 마운트되어 재사용됩니다.

**방법 A — 로컬 서버 (권장, compose가 포트 노출)**

```bash
docker compose run --rm remastered auth
```

터미널에 표시되는 안내에 따라 Mac 브라우저에서 **http://localhost:8095** 로 접속합니다 (`docker-compose.yml`의 `8095` 포트 매핑). Google **데스크톱 앱** OAuth 클라이언트는 loopback(`localhost`) 리다이렉트를 허용합니다.

**방법 B — 복사·붙여넣기 (포트 매핑 없음)**

```bash
docker compose run --rm remastered auth --console
```

URL을 브라우저에 붙여 넣고, 표시된 인증 코드를 터미널에 다시 붙여 넣습니다.

### 작업 실행

```bash
docker compose run --rm remastered run \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  -r /references/my_ref.wav \
  --title "제목 (Remastered)" \
  --privacy private
```

업로드 없이 로컬만:

```bash
docker compose run --rm remastered run \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  -r /references/my_ref.wav \
  --no-upload
```

볼륨 기본 경로:

| 호스트 | 컨테이너 | 용도 |
|--------|----------|------|
| `./docker/data/work` | `/work` | 캐시·중간 파일 (`REMASTERED_WORK_DIR`) |
| `./docker/data/input` | `/input` | (선택) 입력 파일 |
| `./docker/data/output` | `/output` | (선택) 결과 복사 |
| `./references` | `/references` | 레퍼런스 WAV (`web`은 쓰기 가능, CLI는 읽기 전용) |
| `./docker/config` | `/config` | `config.toml`, `client_secrets.json`, `token.json` |

환경 변수로 경로 변경: `REMASTERED_CONFIG_DIR`, `REMASTERED_REFERENCES_DIR`, `REMASTERED_WORK_DIR` 등 (`docker-compose.yml` 참고).

### 멀티 플랫폼 빌드 (선택)

```bash
docker buildx build --platform linux/arm64,linux/amd64 -t remastered:local --target runtime .
```

### 컨테이너에서 테스트

```bash
docker compose --profile test build test
docker compose --profile test run --rm test
```

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
├── Dockerfile              # runtime / dev(test) 멀티 타깃
├── docker-compose.yml      # CLI 일회 실행 + OAuth 포트
├── docker/                 # 예시 설정·데이터 디렉터리
├── pyproject.toml          # 패키지·의존성·CLI 엔트리포인트
├── README.md
├── src/remastered/
│   ├── cli.py              # Click CLI (download / master / render / upload / auth / run)
│   ├── web/                # FastAPI + Jinja2 + htmx 웹 UI
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
