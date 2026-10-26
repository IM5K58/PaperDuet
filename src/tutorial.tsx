import { useEffect, useRef, useState } from 'react';
import { BrandMark, Icon } from './icons';
import { resolveTheme } from './theme';
import { isMac, shortcut } from './platform';
import './tutorial.css';

type Destination = 'connection' | 'library' | 'settings';
type Scene = {
  id: string;
  label: string;
  flow: [string, string, string];
  steps: [string, string, string];
  note: string;
  action: Destination;
  actionLabel: string;
};

const SCENES: Scene[] = [
  {
    id: 'connect', label: 'AI 연결',
    flow: ['연결 방식 선택', isMac ? 'API 키 저장' : '키 저장 · CLI 로그인', '연결 확인'],
    steps: [
      '‘AI 연결 설정’에서 제공자와 연결 방식을 고릅니다.',
      isMac ? 'macOS 베타에서는 API 키를 입력하고 ‘키 저장’을 누릅니다.' : 'API 키를 입력하고 ‘키 저장’을 누릅니다. 공식 CLI는 터미널에서 먼저 로그인합니다.',
      '‘연결 확인’이 완료되면 논문을 가져올 준비가 끝납니다.',
    ],
    note: 'API 요금은 구독과 별도입니다. 논문 내용은 번역·질문·요약을 시작할 때 선택한 AI로 전송됩니다.',
    action: 'connection', actionLabel: 'AI 연결 설정 열기',
  },
  {
    id: 'import', label: '논문 가져오기',
    flow: ['PDF · arXiv', '번역·주석 시작', '책장에서 읽기'],
    steps: [
      '서재의 ‘논문 추가’에서 PDF를 선택하거나 arXiv ID·주소를 넣습니다.',
      '가져온 뒤 처리 창에서 예상 사용량을 확인하고 ‘번역·주석 시작’을 누릅니다.',
      '준비된 논문은 책장의 표지를 눌러 엽니다. 원문은 번역 전에도 읽을 수 있습니다.',
    ],
    note: 'PDF는 최대 150MB까지 지원합니다. 서재 화면 어디에 끌어 놓아도 가져올 수 있어요.',
    action: 'library', actionLabel: '서재에서 논문 추가하기',
  },
  {
    id: 'ask', label: 'Ask AI',
    flow: ['문장 선택', 'Ask AI', '후속 질문'],
    steps: [
      `문장을 드래그한 뒤 ‘Ask AI’를 누르면 선택한 문장으로 새 대화를 엽니다. ${shortcut('K')}로도 열 수 있습니다.`,
      '질문을 쓰거나 ‘쉽게 설명’으로 질문 칸을 채운 뒤 ‘질문 보내기’를 누릅니다.',
      '답변을 읽고 같은 대화에서 질문을 이어갑니다. 좋은 답변은 메모장에 추가하세요.',
    ],
    note: '하단 Ask AI는 현재 대화를 이어 엽니다. 문장을 선택해 Ask AI를 열면 항상 새 대화가 시작되며, 기존 대화와 질문 초안은 대화 목록에 남습니다.',
    action: 'library', actionLabel: '서재에서 논문 열기',
  },
  {
    id: 'summary', label: '요약·흐름',
    flow: ['요약 만들기', '문제 → 방법 → 결과', '원문 근거 확인'],
    steps: [
      '리더의 ‘요약’ 탭에서 예상 사용량을 확인하고 ‘요약 만들기’를 누릅니다.',
      '핵심 요약과 섹션 흐름을 읽습니다. 근거를 누르면 해당 본문으로 이동합니다.',
      '필요한 설명은 ‘고치기’로 다듬거나 ‘메모장에 추가’로 모아 둡니다.',
    ],
    note: '만든 요약은 저장됩니다. 중요한 수치와 주장은 연결된 원문 근거에서 확인하세요.',
    action: 'library', actionLabel: '서재에서 논문 열기',
  },
  {
    id: 'memo', label: '메모장',
    flow: ['메모장 열기', '자유롭게 작성', '논문별 저장'],
    steps: [
      '리더 아래의 ‘메모장’을 눌러 창을 엽니다. 제목을 드래그해 이동하고 오른쪽 아래에서 크기를 조절합니다.',
      '제목과 내용을 자유롭게 적습니다. AI 답변과 요약도 ‘메모장에 추가’로 모을 수 있습니다.',
      '‘메모 저장’을 누르면 이 논문에 저장됩니다. ‘새 메모’로 다른 메모를 만들 수 있습니다.',
    ],
    note: '메모는 논문마다 따로 보관됩니다. 저장 전 내용은 이 PC에 임시 보관됩니다.',
    action: 'library', actionLabel: '서재에서 논문 열기',
  },
  {
    id: 'export', label: '내보내기',
    flow: ['내보내기', '파일 형식 선택', '저장 위치 선택'],
    steps: [
      '리더의 ‘내보내기’를 누릅니다.',
      '읽을 파일은 ‘단일 HTML’, 편집할 문서는 ‘Markdown 대역본’, 메모는 ‘메모장 Markdown’을 고릅니다.',
      '‘저장 위치 선택’을 눌러 파일을 저장합니다.',
    ],
    note: '단일 HTML은 인터넷 없이 열 수 있습니다. 내보내기에는 AI를 사용하지 않습니다.',
    action: 'library', actionLabel: '서재에서 논문 열기',
  },
  {
    id: 'settings', label: '저장',
    flow: ['이 PC에 보관', '읽던 위치 기억', '필요하면 폴더 변경'],
    steps: [
      '가져온 논문과 저장한 번역·요약·대화·메모는 이 PC에 보관됩니다.',
      '읽던 위치는 자동으로 기억합니다. 서재의 ‘이어서 읽기’로 돌아오세요.',
      '저장 위치를 바꾸려면 ‘앱 설정’에서 ‘새 저장 폴더 선택’을 누릅니다.',
    ],
    note: '새 폴더로 복사·검증한 뒤 앱을 재시작합니다. 기존 폴더는 그대로 보관됩니다.',
    action: 'settings', actionLabel: '저장 위치 확인하기',
  },
];

const PROGRESS_KEY = 'paperduet-tutorial-v1';
const ALIASES: Record<string, string> = {
  translate: 'import', read: 'import', original: 'import', review: 'ask', storage: 'settings', present: 'memo',
};
const validScene = (id: string) => SCENES.some(scene => scene.id === id);
function sceneFor(id: unknown): string | undefined {
  if (typeof id !== 'string') return undefined;
  return validScene(id) ? id : Object.hasOwn(ALIASES, id) ? ALIASES[id] : undefined;
}
function loadProgress(): { last: string; done: string[] } {
  try {
    const saved = JSON.parse(localStorage.getItem(PROGRESS_KEY) || 'null');
    const done = Array.isArray(saved?.done)
      ? [...new Set<string>(saved.done.flatMap((id: unknown) => sceneFor(id) ? [sceneFor(id)!] : []))]
      : [];
    return { last: sceneFor(saved?.last) || 'connect', done };
  } catch {
    return { last: 'connect', done: [] };
  }
}
export const lastTutorialScene = () => loadProgress().last;

export function Tutorial({ sceneId, onScene, onGo }: {
  sceneId: string; onScene: (id: string) => void; onGo: (target: Destination) => void;
}) {
  const canonical = sceneFor(sceneId) || 'connect';
  const index = SCENES.findIndex(scene => scene.id === canonical);
  const scene = SCENES[index];
  const [done, setDone] = useState(loadProgress().done);
  const [theme, setTheme] = useState(resolveTheme(document.documentElement.dataset.theme));
  const title = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);

  useEffect(() => {
    const previous = document.documentElement.dataset.theme;
    return () => {
      if (previous) document.documentElement.dataset.theme = previous;
      else delete document.documentElement.dataset.theme;
    };
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    try { localStorage.setItem(PROGRESS_KEY, JSON.stringify({ last: scene.id, done })); }
    catch { /* The guide remains readable without storage. */ }
  }, [scene.id, done]);
  useEffect(() => {
    if (canonical !== sceneId) {
      const params = new URLSearchParams(location.search);
      params.set('tutorial', canonical);
      history.replaceState(null, '', `?${params}`);
    }
  }, [canonical, sceneId]);
  useEffect(() => {
    if (mounted.current) {
      title.current?.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: 'instant' });
    }
    mounted.current = true;
  }, [scene.id]);

  const next = () => {
    setDone(previous => previous.includes(scene.id) ? previous : [...previous, scene.id]);
    if (index < SCENES.length - 1) onScene(SCENES[index + 1].id);
    else onGo('library');
  };

  return <div className="tutorial-page">
    <a className="skip-link" href="#tutorial-scene-title">본문으로 이동</a>
    <header className="tutorial-header">
      <a className="brand" href="?library=1" onClick={event => { event.preventDefault(); onGo('library'); }}>
        <BrandMark/><b>PaperDuet</b>
      </a>
      <div className="tutorial-header-actions">
        <button className="icon-button" aria-label="가이드 테마 변경" title="테마 변경"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
          <Icon name={theme === 'dark' ? 'sun' : 'moon'}/>
        </button>
        <button onClick={() => onGo('library')}>서재로</button>
      </div>
    </header>

    <div className="tutorial-layout">
      <aside className="tutorial-sidebar">
        <h1>사용 가이드</h1>
        <nav aria-label="가이드 설명">
          {SCENES.map((item, position) => <button key={item.id}
            aria-label={`${position + 1} ${item.label}`}
            aria-current={item.id === scene.id ? 'step' : undefined}
            onClick={() => onScene(item.id)}>
            <span className="tutorial-step-number">{position + 1}</span>{item.label}
          </button>)}
        </nav>
      </aside>

      <main className="tutorial-main">
        <label className="tutorial-mobile-index">설명 선택
          <select value={scene.id} onChange={event => onScene(event.target.value)}>
            {SCENES.map((item, position) => <option key={item.id} value={item.id}>{position + 1}. {item.label}</option>)}
          </select>
        </label>
        <div className="tutorial-scene-intro">
          <span className="tutorial-position">{String(index + 1).padStart(2, '0')} / {String(SCENES.length).padStart(2, '0')}</span>
          <h2 id="tutorial-scene-title" ref={title} tabIndex={-1}>{scene.label}</h2>
        </div>

        <ol className="tutorial-flow" aria-label={`${scene.label} 사용 흐름`}>
          {scene.flow.map((label, position) => <li key={label}>
            {position > 0 && <Icon name="arrowRight"/>}<span>{label}</span>
          </li>)}
        </ol>

        <ol className="tutorial-steps" aria-label="사용 방법">
          {scene.steps.map((step, position) => <li key={step}>
            <span aria-hidden="true">{position + 1}</span><p>{step}</p>
          </li>)}
        </ol>
        {scene.id === 'connect' && <details className="tutorial-cli">
          <summary>공식 CLI 로그인 명령 보기</summary>
          <dl>
            <div><dt>Claude Code</dt><dd><code>claude auth login</code></dd></div>
            <div><dt>Codex</dt><dd><code>codex login</code></dd></div>
          </dl>
        </details>}
        <p className="tutorial-note">{scene.note}</p>

        <button className="primary-button tutorial-open" onClick={() => onGo(scene.action)}>
          {scene.actionLabel}<Icon name="arrowRight"/>
        </button>
        <nav className="tutorial-navigation" aria-label="설명 이동">
          <button disabled={index === 0} onClick={() => onScene(SCENES[index - 1].id)}>
            <Icon name="chevronLeft"/>이전
          </button>
          <button onClick={next}>{index < SCENES.length - 1 ? '다음' : '서재로 시작하기'}
            <Icon name={index < SCENES.length - 1 ? 'chevronRight' : 'arrowRight'}/>
          </button>
        </nav>
      </main>
    </div>
  </div>;
}
