# macOS 베타 개발 및 검증

현재 macOS 지원은 **macOS 14 이상, Apple Silicon 우선**인 소스 단계 베타입니다. Windows 버전과 같은 React 화면·Python 처리·SQLite 형식을 사용합니다. Windows 환경에서 플랫폼 경계와 WebKit을 검사할 수 있지만, macOS 네이티브 앱 실행과 키체인 권한 확인을 대체하지는 않습니다. 이 문서를 추가한 시점에는 맥 CI와 실기기 검증이 아직 실행되지 않았습니다.

## 지원 범위

- PDF/arXiv 가져오기, 썸네일 책장, 원문·대역·번역 읽기
- Anthropic/OpenAI/Google API 키 연결, 번역·주석·요약·Ask AI
- macOS 키체인에만 API 키 저장. 평문 파일·환경변수 키 저장으로 대체하지 않음
- 논문별 메모, HTML/Markdown 내보내기, 저장 위치 변경
- Command 단축키, 이동·네 변/모서리 크기 조절 플로팅 창
- 앱 종료 시 백엔드 프로세스 그룹 정리. 앱이 비정상 종료돼도 수명 파이프의 EOF로 백엔드와 작업자를 종료

**공식 CLI 연결은 맥 베타에서 비활성화**했습니다. 현재 CLI 탐지·프로세스 격리는 Windows 구현이므로, 빌드 정책을 바꿔도 맥에서 활성화되지 않습니다. 구독 CLI 지원은 별도 후속 작업입니다. Intel용 빌드와 macOS 13 이하는 이번 베타 검증 대상이 아닙니다.

## 준비 및 실행

Mac에 Xcode Command Line Tools, Node.js 22.12 이상, Python 3.12, Rust stable을 준비합니다. Apple Silicon에서는 Python과 Rust도 arm64로 설치하고 Rosetta 환경과 섞지 않습니다.

```bash
xcode-select --install
PYTHON=python3.12 bash scripts/setup-macos.sh
npm run dev
```

최초 준비는 가상환경·npm 의존성과 macOS용 PyInstaller 사이드카를 만듭니다. 사이드카는 다른 OS의 바이너리로 대체할 수 없습니다. Python 코드를 바꿨다면 개발 앱을 닫고 `npm run build:sidecar`를 실행한 뒤 다시 켭니다. 교체하지 않고 생성만 확인하려면 `npm run build:sidecar -- --stage-only`를 사용합니다.

```bash
npm run build
```

현재 Mac의 아키텍처로 `.app`과 `.dmg`를 만듭니다. 결과는 `src-tauri/target/release/bundle/macos/`와 `src-tauri/target/release/bundle/dmg/`입니다. 로컬 빌드는 기본적으로 ad-hoc 서명하며 자동 업데이트 배포 경로는 연결하지 않습니다.

## 데이터와 OS 연동

- 정식 앱 기본 위치: `~/Library/Application Support/PaperDuet`
- 개발 앱: `~/Library/Application Support/PaperDuet-dev` (설치 앱의 서재를 자동 업그레이드하지 않도록 분리)
- 읽기·검색·Ask AI는 `⌘+1/2/3`, `⌘+F`, `⌘+K`, `⌘+Enter`; 메모는 `⌘+S`로 저장
- 첫 키체인 사용 시 macOS가 접근 권한을 물을 수 있음. 취소·잠금 상태는 오류로 표시하고 평문 저장으로 우회하지 않음
- Windows 키는 자격 증명 관리자, Mac 키는 키체인에 있으므로 서재 폴더를 복사해도 API 키는 따로 연결해야 함
- Tauri 정식 Mac Origin은 `tauri://localhost`, Windows는 `http://tauri.localhost`. 백엔드는 지정된 Origin만 허용

## 검증

```bash
npm run test:backend
npm run build:web
.venv/bin/python scripts/smoke-sidecar.py src-tauri/resources/backend/paperduet-backend
PAPERDUET_TEST_SIDECAR="$PWD/src-tauri/resources/backend/paperduet-backend" cargo test --locked --manifest-path src-tauri/Cargo.toml
npx playwright install chromium webkit
npx playwright test --config playwright.macos.config.ts
```

macOS 전용 Playwright 검증은 공개 저장소에 없는 샘플 대신 작은 테스트 논문을 만들고, 실제 FastAPI/SQLite와 테스트 AI 전송을 사용합니다. Command 검색·보기·선택 질문·질문 전송·메모 저장, 창 조절, 키체인 안내와 API 전용 연결을 Chromium/WebKit 양쪽에서 확인합니다. 실제 API 키나 유료 AI 요청은 사용하지 않습니다.

실제 키체인 권한까지 검사하려면 Mac에서 `PAPERDUET_TEST_KEYCHAIN=1 npm run test:backend -- -k native_macos_keychain_roundtrip`을 실행합니다. 임의 이름의 테스트 항목 하나만 생성·조회·삭제하며 실제 앱의 API 키는 읽거나 수정하지 않습니다. OS 권한 창이 나올 수 있어 CI에서는 기본적으로 건너뜁니다.

`.github/workflows/macos.yml`은 macOS 15 arm64 러너에서 의존성 설치, 백엔드·프로세스 수명 테스트, WebKit/Chromium 테스트, app/DMG 빌드, 서명 확인, 앱 내부 사이드카 스모크를 실행합니다. 현재 Playwright와 macOS 14용 고정 WebKit 사이의 [페이지 초기화 호환성 문제](https://github.com/microsoft/playwright/issues/42964) 때문에 CI는 macOS 15를 사용합니다. 앱의 배포 대상 설정은 macOS 14 이상이며, macOS 14에서의 실제 실행 여부는 별도로 확인해야 합니다. 결과는 Actions 아티팩트로만 보관하며 릴리스 게시·업로드는 하지 않습니다. 커밋·푸시하거나 해당 워크플로를 실행하기 전에는 원격 검증이 실행되지 않습니다.

공개 전 실제 Mac에서 Finder로 앱 열기, 키체인 저장/조회/삭제·권한 거절, 한글 입력, PDF 드롭, 목차·선택·그림 질문·창 조절, 저장/내보내기 다이얼로그, 앱 종료/강제 종료 후 남은 백엔드가 없는지 확인해야 합니다. Playwright WebKit은 실제 WKWebView와 OS 권한 검사의 대체 수단이 아닙니다.

## 공개 배포

ad-hoc 빌드는 개발·테스트용이며 Apple이 확인한 배포본이 아닙니다. 일반 사용자용 배포에는 Developer ID Application 인증서와 Apple 공증이 필요합니다. `APPLE_SIGNING_IDENTITY`에 실제 인증서 이름을 지정하면 사이드카의 내장 Python·확장 라이브러리와 Tauri 앱에 같은 서명 주체를 사용합니다. Apple 공증 자격 증명은 Tauri 공식 안내에 따라 환경/CI 비밀 값으로 전달하고 저장소에 넣지 않습니다.

인증서나 Apple 자격 증명을 이 작업에서 생성·수집하지 않았습니다. Windows의 `scripts/build-release.ps1`/`scripts/release.ps1`는 Windows 릴리스 경로로 유지했습니다. 맥 자동 업데이트를 공개할 때는 별도의 macOS 아티팩트 URL·업데이터 서명·`darwin-aarch64` 매니페스트가 필요하며, 현재 맥 로컬 빌드는 기존 Windows 업데이트 서버에 연결하지 않습니다.

- Tauri 플랫폼 설정: https://v2.tauri.app/reference/config/#platform-specific-configuration
- macOS 서명·공증: https://v2.tauri.app/distribute/sign/macos/
- PyInstaller macOS 서명: https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing
