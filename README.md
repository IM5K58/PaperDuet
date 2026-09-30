# PaperDuet

**영어 논문을 한국어 대역 리더로 변환합니다.** PDF나 arXiv 링크를 넣으면 원문과 번역을 한 문단씩 나란히 보여 주고 핵심·결과·한계를 짚는 맥락 주석을 달아 줍니다. 읽다가 막히는 문장은 바로 AI에게 물어볼 수 있습니다.

Windows 10/11 데스크톱 앱이며, 논문과 번역은 모두 내 PC에만 저장됩니다.

> **최신 버전: 0.7.0.** 앱을 켜자마자 서재가 뜨지 않던 문제를 고치고, 설정 화면과 사용 가이드를 다듬었습니다. 자세한 내용은 [릴리스 노트](../../releases/tag/v0.7.0)에 있습니다.

## 주요 기능

- **대역 읽기**: 원문 · 대역 · 번역 세 가지 보기(`Ctrl+1/2/3`), 읽기 편한 세리프 본문, 목차, 본문 검색(`Ctrl+F`), 글자 크기 조절, 라이트/다크 테마. 문단에 마우스를 올리면 원문과 번역이 함께 강조됩니다.
- **서재**: 마지막으로 읽던 논문을 바로 여는 **이어 읽기**, 논문별 처리 상태 표시, 화면 어디에나 PDF를 끌어 놓아 가져오기
- **전문 번역**: 용어집을 먼저 만든 뒤 논문 전체를 일관된 용어로 번역합니다. 참고문헌은 원문을 유지합니다.
- **표·수식 복원**: 병합 셀이 있는 표와 LaTeX 수식을 복원하고 원본 PDF 조각과 비교해 볼 수 있습니다.
- **맥락 주석**: 핵심 아이디어·결과·한계·통찰·방법·용어의 6가지 색 주석. 카드마다 접었다 펼 수 있습니다.
- **AI에게 질문**: 문장을 선택하고 `Ctrl+K`로 질문하거나 쉽게 설명·요약을 요청합니다. 답변은 주석이나 발표 노트로 저장할 수 있습니다.
- **arXiv 가져오기**: arXiv ID나 URL만 넣으면 HTML 원문의 표와 수식을 그대로 가져옵니다.
- **내보내기**: 인터넷 없이 열리는 단일 HTML 리더, Markdown 대역본, 발표 노트
- **자동 검수**: 번역 누락·숫자 불일치 같은 문제가 의심되는 블록에 "검수 필요" 표시를 붙입니다.

## 설치

### 1. 설치 파일 받기

[Releases](../../releases/latest) 페이지에서 최신 설치 파일(현재 `PaperDuet_0.7.0_x64-setup.exe`)을 내려받습니다. 받은 파일은 릴리스 노트의 SHA-256 값으로 확인할 수 있습니다.

- 지원 환경: Windows 10/11 64비트
- 관리자 권한 없이 현재 사용자 계정에만 설치됩니다.
- Python, Node.js 등 별도 프로그램은 필요 없습니다. 실행에 필요한 WebView2가 없으면 설치 중에 자동으로 받습니다(인터넷 필요).

### 2. 설치 파일 실행

이 앱은 코드 서명이 되어 있지 않아 Windows가 경고를 띄울 수 있습니다.

1. "Windows의 PC 보호" 창이 뜨면 **추가 정보**를 누릅니다.
2. **실행**을 누릅니다.
3. 설치가 끝나면 시작 메뉴에서 **PaperDuet**을 실행합니다.

### 3. AI 연결

첫 실행 때 AI 연결 방식을 고릅니다. 연결하지 않아도 원본 PDF와 추출한 원문은 읽을 수 있습니다.

| 방식 | 준비물 | 비용 |
| --- | --- | --- |
| **API 키** | Anthropic, OpenAI 또는 Google AI Studio API 키 | 사용량만큼 API 요금 |
| **공식 CLI** | [Claude Code](https://code.claude.com/docs/en/setup) 또는 [Codex CLI](https://developers.openai.com/codex/cli/) 설치 후 로그인 | 로그인한 구독 요금제 한도 안에서 사용 |

**API 키**: 키를 붙여 넣고 **연결 확인**을 누릅니다. 키는 Windows 자격 증명 관리자에만 저장되고 앱 데이터베이스에는 저장되지 않습니다.

**공식 CLI**: 앱은 로그인을 대신하지 않습니다. PowerShell에서 먼저 로그인한 뒤, 앱에서 CLI를 선택하고 **연결 확인**을 누릅니다.

```powershell
claude auth login
```

```powershell
codex login
```

CLI를 찾지 못하면 앱 설정에서 실행 파일 경로를 직접 지정할 수 있습니다.

## 사용법

1. **논문 가져오기**: 서재 화면 어디에나 PDF를 끌어 놓거나 상단의 **+ 논문 추가**를 누릅니다. arXiv 논문은 서재의 arXiv 입력칸에 `2603.07952v1` 같은 ID나 URL을 넣고 **arXiv 가져오기**를 누릅니다.
2. **처리 시작**: **처리 상태**에서 단계별 모델과 예상 사용량을 확인하고 **번역·주석 시작**을 누릅니다. 처리 중에 창을 닫아도 백그라운드에서 계속되고 상단 알림으로 진행률을 보여 줍니다.
3. **읽기**: 완료된 논문을 열어 대역으로 읽습니다. 서재 맨 위의 **이어 읽기**에서 마지막으로 읽던 논문을 바로 열 수 있습니다. 처음이라면 서재의 **사용 가이드**에서 10개 설명으로 기능을 둘러볼 수 있습니다. Releases의 설치 파일에는 번역·주석이 완성된 샘플 논문(Rex-Omni)이 들어 있습니다.
4. **검수**: "검수 필요" 표시가 붙은 블록은 원문과 비교해 직접 고치거나 다시 생성합니다.
5. **질문과 내보내기**: 문장을 선택하거나 문단 옆의 **질문** 버튼으로 AI에게 묻고, 논문 제목 아래의 **내보내기**로 HTML이나 Markdown 파일로 저장합니다.

### 사용량

한 편을 처리할 때 AI 호출은 표·수식 복원, 용어집, 번역, 주석 단계로 나뉩니다. 호출을 묶고 필요한 문맥만 보내도록 설계해서 48쪽 논문 기준 약 20만 토큰을 목표로 합니다(오프라인 추정치). 실제 사용량은 모델과 논문에 따라 다르며, 처리 후 **처리 상태 → 실제 AI 사용량**에서 단계별로 확인할 수 있습니다.

## 데이터와 개인정보

- 논문 원본, 번역, 주석, 대화는 `%APPDATA%\PaperDuet`에 저장됩니다. **앱 설정 → 새 저장 폴더 선택**으로 옮길 수 있습니다.
- AI로 데이터가 전송되는 것은 **번역·주석 시작**을 누르거나 질문을 보낼 때뿐입니다. 연결 확인은 문서를 보내지 않습니다.
- 앱의 로컬 서버는 이 PC 안(루프백 주소)에서만 동작하며 세션 토큰이 없는 요청은 거부합니다.

## 업데이트와 제거

- **업데이트**: 새 버전의 설치 파일을 내려받아 그대로 실행하면 됩니다. 이전 버전을 먼저 제거할 필요가 없고 서재 데이터는 유지됩니다. 자동 업데이트는 아직 지원하지 않습니다.
- **제거**: Windows **설정 → 앱**에서 PaperDuet을 제거합니다. 저장한 논문은 기본적으로 남겨 둡니다.

## 문제 해결

| 증상 | 해결 |
| --- | --- |
| 설치 파일이 실행되지 않음 | SmartScreen 경고에서 **추가 정보 → 실행**을 누릅니다. |
| CLI 연결이 안 됨 | PowerShell에서 `claude --version` 또는 `codex --version`이 동작하는지, 로그인했는지 확인합니다. |
| "검수 필요"가 많음 | 자동 검사를 통과하지 못한 블록입니다. 원문과 비교해 고치거나, 더 강한 모델로 다시 생성합니다. |
| 표나 수식이 깨짐 | arXiv 논문은 PDF 대신 arXiv ID로 가져오면 HTML 원문의 표와 수식을 씁니다. |

스캔한 이미지 PDF(OCR)와 모든 출판사 레이아웃을 완벽하게 지원하지는 않습니다.

## 소스에서 빌드하기

### 준비물

- Windows 10/11 x64
- Node.js 22.12 이상, Python 3.12
- Rust stable (MSVC), Visual Studio 2022 C++ Build Tools와 Windows SDK
- WebView2 런타임

### 설정과 실행

프로젝트 루트의 PowerShell에서 처음 한 번 실행합니다. Python 가상환경과 npm 패키지를 설치하고 백엔드 사이드카를 빌드합니다.

```powershell
.\scripts\setup.ps1
```

개발 모드로 실행합니다.

```powershell
npm run dev
```

Python 백엔드를 고친 뒤에는 `npm run build:sidecar`로 다시 번들하고 앱을 재시작합니다.

### 설치 파일 만들기

```powershell
npm run build
```

결과물은 `src-tauri/target/release/bundle/nsis/PaperDuet_<버전>_x64-setup.exe`입니다.

### 테스트

```powershell
npm run test:backend
```

```powershell
npx playwright install chromium
```

```powershell
npm run test:e2e
```

실제 유료 AI 호출은 자동 테스트에 포함되지 않습니다. 샘플 논문 데이터가 없는 클론에서는 샘플을 읽는 백엔드 테스트 10개와 E2E 테스트를 건너뜁니다.

### 구조

| 경로 | 내용 |
| --- | --- |
| `src/` | React + TypeScript 화면. 디자인 토큰은 `src/styles/tokens.css` |
| `src-tauri/` | Tauri 2 데스크톱 셸(Rust) |
| `backend/paperduet/` | Python FastAPI 사이드카: PDF 파싱, AI 파이프라인, 저장소 |
| `backend/paperduet/prompts/` | 단계별 AI 프롬프트 |
| `tests/`, `backend/tests/` | Playwright E2E, pytest |
| `docs/licenses/` | 서드파티 라이선스 전문 |

## 라이선스

PaperDuet은 [GNU Affero General Public License v3.0](LICENSE)으로 배포합니다. 수정한 버전을 배포하거나 네트워크 서비스로 제공할 때는 소스 코드도 같은 라이선스로 공개해야 합니다.

설치 파일에 포함되는 주요 서드파티 라이선스 전문은 [`docs/licenses/`](docs/licenses/)에 있습니다.

- PDF 파싱: [PyMuPDF](https://pymupdf.readthedocs.io/) (AGPL-3.0)
- 글꼴: Inter, IBM Plex Sans KR, Source Serif 4, Noto Serif KR (SIL Open Font License 1.1). 수식 렌더링: KaTeX (MIT)

### 샘플 논문

설치 파일의 샘플은 Qing Jiang, Junan Huo, Xingyu Chen, Yuda Xiong, Zhaoyang Zeng, Yihao Chen, Tianhe Ren, Junzhi Yu, Lei Zhang, "[Detect Anything via Next Point Prediction](https://arxiv.org/abs/2510.12798)" (arXiv:2510.12798, 2025)의 1–31쪽 본문을 한국어로 번역하고 주석을 단 것입니다. 원 논문은 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)으로 공개되어 있습니다.

샘플 데이터(`fixtures/rex-omni.blocks.json`)는 이 소스 저장소에 포함하지 않습니다. 저장소에서 직접 빌드한 앱에는 샘플이 없고, 샘플이 필요한 테스트는 자동으로 건너뜁니다.
