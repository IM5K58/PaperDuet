import { invoke } from '@tauri-apps/api/core';

interface Connection { port: number; token: string; generation: number }
export const ERROR_TEXT: Record<string,string> = {
  ARXIV_INVALID:'올바른 arXiv ID 또는 HTTPS 링크를 입력해 주세요.',ARXIV_DOWNLOAD_FAILED:'arXiv 원문을 가져오지 못했습니다. ID·네트워크를 확인하고 다시 시도해 주세요.',ARXIV_TOO_LARGE:'arXiv 파일이 허용 크기를 초과했습니다.',STORAGE_BUSY:'번역·질문을 마친 뒤 저장 위치를 변경해 주세요.',STORAGE_MAINTENANCE:'저장 위치를 전환 중입니다. 잠시 기다려 주세요.',EXPORT_NOT_BUILT:'내보내기 리더가 빌드되지 않았습니다.',BATCH_EXPIRED:'제공자가 24시간 안에 처리하지 못한 요청이 있습니다. 이어서 실행하거나 바로 처리로 마저 진행해 주세요.',BATCH_UNSUPPORTED:'절약 모드는 API 키로 연결했을 때만 쓸 수 있습니다.',NOT_IN_SAVING_MODE:'이미 바로 처리로 진행 중입니다.',

  AI_CONNECTION_REQUIRED: 'AI 연결 설정에서 API 키 또는 공식 CLI를 연결해 주세요.', AI_AUTH_FAILED: 'API 키를 확인해 주세요.',
  CLI_NOT_FOUND:'공식 CLI를 찾지 못했습니다. 설치하거나 실행 파일 경로를 지정해 주세요.', CLI_DISABLED:'이 빌드에서는 CLI 연결을 지원하지 않습니다.', CLI_UNSUPPORTED:'이 제공자는 API 키 연결만 지원합니다.', CLI_UPDATE_REQUIRED:'CLI 안전 실행 옵션이 부족합니다. 공식 CLI를 업데이트해 주세요.', CLI_REQUEST_FAILED:'CLI 요청이 실패했습니다. 공식 CLI에서 로그인 상태와 모델을 확인해 주세요.', CLI_ISOLATION_FAILED:'CLI 격리 프로세스를 만들지 못했습니다.', CLI_TIMEOUT:'CLI 응답 시간이 초과되었습니다.', AI_OUTPUT_LIMIT:'응답 길이 한도에 도달했습니다. 범위를 줄여 다시 질문해 주세요.', AI_STREAM_INTERRUPTED:'응답이 중단되었습니다. 다시 질문하거나 재생성해 주세요.', SELECTION_CHANGED:'선택한 텍스트가 달라졌습니다. 다시 선택해 주세요.', THREAD_BUSY:'이 대화에서 답변을 생성 중입니다.', CONTEXT_TOO_LARGE:'문맥이 너무 큽니다. 논문 전체 포함을 꺼 주세요.', ANSWER_TOO_LONG:'주석 길이 한도를 초과했습니다. 짧은 답변으로 다시 요청해 주세요.',
  AI_OVERLOADED: 'AI 제공자 서버가 혼잡해 여러 번 다시 시도했지만 응답이 없었습니다. 잠시 후 이어서 실행해 주세요.', AI_MODEL_NOT_FOUND: '선택한 모델을 찾을 수 없습니다. AI 연결 설정에서 모델 이름을 확인해 주세요.',
  AI_RATE_LIMIT: '사용량 한도에 도달했습니다. 잠시 후 이어서 실행해 주세요.', AI_REQUEST_FAILED: 'AI 요청이 거부되었습니다. 모델과 API 사용 가능 여부를 확인해 주세요.',
  AI_NETWORK_ERROR: 'AI에 연결하지 못했습니다. 네트워크를 확인하고 재개해 주세요.', AI_INVALID_JSON: 'AI 응답 형식이 맞지 않습니다. 다시 시도해 주세요.',
  KEYRING_UNAVAILABLE: 'Windows 자격 증명 관리자에 접근하지 못했습니다.', INVALID_KEY_FORMAT: 'API 키 형식을 확인해 주세요.',
  INVALID_PDF: '열 수 없는 PDF입니다. 파일이 손상되었는지 확인해 주세요.', UNSUPPORTED_PDF: '암호가 있거나 지원 범위를 벗어난 PDF입니다.', PDF_TOO_LARGE: 'PDF는 150MB 이하만 지원합니다.',
  DOCUMENT_NOT_READY: '본문 추출이 끝난 뒤 요약을 만들 수 있습니다.', SUMMARY_RUNNING: '요약을 만들고 있습니다. 끝난 뒤 다시 시도해 주세요.', SUMMARY_NOT_FOUND: '먼저 요약을 만들어 주세요.',
  JOB_NOT_RESUMABLE: '작업 상태를 새로 확인한 뒤 다시 실행해 주세요.', JOB_BUSY: '처리를 일시정지한 뒤 수정해 주세요.', PIPELINE_FAILED: '이 단계 처리에 실패했습니다. 저장된 지점부터 다시 시도할 수 있습니다.',
};

export async function authorizedFetch(path: string, options: RequestInit = {}) {
  const connection = await invoke<Connection>('backend_connection');
  return fetch(`http://127.0.0.1:${connection.port}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${connection.token}`, ...(options.body && typeof options.body === 'string' ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
}

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  // Native IPC is the only source of the ephemeral bearer token. Never persist
  // it to storage, log it, or put it in a URL. Re-read after sidecar restarts.
  const response = await authorizedFetch(path, { ...options, signal: options.signal ?? AbortSignal.timeout(path.startsWith('/providers/health') ? 90000 : path.endsWith('/regenerate') ? 900000 : path === '/documents' ? 120000 : 20000) });
  if (!response.ok) {
    const value = await response.json().catch(() => ({}));
    throw new Error(ERROR_TEXT[value.detail] ?? `로컬 요청 실패 (${response.status})`);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
