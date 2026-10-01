import { invoke } from '@tauri-apps/api/core';
import { useState } from 'react';
import creator from './assets/creator.png';
import { Modal } from './connections';
import { Icon } from './icons';

export const LINKS = {
  releases: 'https://github.com/IM5K58/PaperDuet/releases/latest',
  source: 'https://github.com/IM5K58/PaperDuet',
  bugs: 'https://forms.gle/aFKGWLuKtdAFjwHV6',
  vierasion: 'https://vierasion.com/',
} as const;

// The desktop shell opens only these addresses in the default browser (desktop.rs LINKS).
export function openLink(url: string) {
  void invoke('open_link', { url }).catch(() => window.open(url, '_blank', 'noopener'));
}

/** Bottom of 앱 설정: release notes, creator, terms, bug reports. */
export function AboutLinks() {
  const [page, setPage] = useState<'creator' | 'terms' | null>(null);
  const row = (label: string, hint: string, onClick: () => void, external = false) =>
    <li><button onClick={onClick}><span><b>{label}</b><small>{hint}</small></span>{external ? <span className="about-external" aria-label="새 창에서 열기">↗</span> : <Icon name="chevronRight" />}</button></li>;
  return <>
    <h3>정보</h3>
    <ul className="about-links">
      {row('릴리스 노트', 'GitHub에서 최신 버전의 변경 사항을 봅니다', () => openLink(LINKS.releases), true)}
      {row('제작자 정보', 'Oh In Kyum · vierasion', () => setPage('creator'))}
      {row('이용약관', '앱 이용 조건과 데이터 처리 안내', () => setPage('terms'))}
      {row('버그 제보', '문제나 의견을 양식으로 보냅니다', () => openLink(LINKS.bugs), true)}
    </ul>
    {page === 'creator' && <Modal title="제작자 정보" onClose={() => setPage(null)}><div className="dialog-content creator">
      <img className="creator-photo" src={creator} alt="Oh In Kyum 프로필 이미지" width={96} height={96} />
      <h3>Oh In Kyum</h3>
      <p className="creator-role">vierasion 대표</p>
      <div className="dialog-actions creator-actions">
        <button className="secondary-button" onClick={() => openLink(LINKS.vierasion)}>vierasion.com ↗</button>
        <button onClick={() => openLink(LINKS.source)}>GitHub 소스 코드 ↗</button>
      </div>
      <p className="creator-note">PaperDuet은 어려운 영어 논문을 한국어로 이해하도록 돕기 위해 만든 오픈소스 앱입니다.</p>
    </div></Modal>}
    {page === 'terms' && <Modal title="이용약관" onClose={() => setPage(null)}><Terms /></Modal>}
  </>;
}

function Terms() {
  return <div className="dialog-content terms">
    <p className="terms-date">시행일: 2026년 10월 1일</p>

    <h4>제1조 (목적)</h4>
    <p>이 약관은 PaperDuet(이하 “앱”)을 이용하는 조건과, 이용자와 제작자(Oh In Kyum, 이하 “제작자”)의 권리와 의무를 정합니다.</p>

    <h4>제2조 (앱의 성격과 라이선스)</h4>
    <ol>
      <li>앱은 개인이 만든 무료 오픈소스 소프트웨어이며, GNU Affero General Public License v3.0(AGPL-3.0)에 따라 배포됩니다. 소스 코드는 GitHub 저장소에서 누구나 볼 수 있습니다.</li>
      <li>앱을 사용·수정·재배포할 수 있는 권리와 조건은 AGPL-3.0을 따릅니다. 이 약관과 AGPL-3.0이 서로 다르면 AGPL-3.0이 우선합니다.</li>
    </ol>

    <h4>제3조 (데이터와 개인정보)</h4>
    <ol>
      <li>제작자는 이용자의 논문, 번역, 주석, AI 대화, 발표 노트를 수집하지 않으며 별도 서버에 저장하지 않습니다. 이 데이터는 이용자의 PC에만 저장됩니다.</li>
      <li>API 키는 Windows 자격 증명 관리자에만 저장되며 제작자에게 전송되지 않습니다.</li>
      <li>이용자가 번역·주석·질문을 실행하면 그 내용이 이용자가 선택한 AI 제공자(Anthropic, OpenAI, Google 등)에 전송됩니다. 이 전송과 처리에는 해당 제공자의 약관과 개인정보 처리방침이 적용됩니다.</li>
      <li>앱은 새 버전을 확인하고 업데이트를 받기 위해 GitHub에 접속합니다. 이때 GitHub의 정책에 따라 접속 정보(IP 주소 등)가 처리될 수 있습니다.</li>
      <li>버그 제보 양식(Google Forms)에 적은 내용은 버그 확인과 답변에만 사용합니다. 꼭 필요한 경우가 아니면 개인정보를 적지 마세요.</li>
    </ol>

    <h4>제4조 (AI 서비스 이용과 비용)</h4>
    <ol>
      <li>AI 기능은 이용자가 직접 연결한 API 키 또는 공식 CLI 계정으로 동작합니다. 사용 요금과 한도는 이용자와 해당 AI 제공자 사이의 계약에 따릅니다.</li>
      <li>앱이 보여 주는 예상 사용량은 추정치이며 실제 청구 금액과 다를 수 있습니다. AI 제공자의 요금, 정책 변경, 서비스 장애는 제작자가 관리할 수 없습니다.</li>
    </ol>

    <h4>제5조 (저작권과 이용자의 책임)</h4>
    <ol>
      <li>이용자가 가져오는 논문과 자료의 저작권은 원저작자에게 있습니다. 이용자는 자신이 적법하게 이용할 수 있는 자료만 앱에서 처리해야 합니다.</li>
      <li>앱으로 만든 번역과 주석은 개인의 학습과 연구를 돕기 위한 것입니다. 이를 공개하거나 재배포할 때는 원문의 라이선스와 저작권법을 이용자가 직접 확인해야 합니다.</li>
      <li>이용자는 법령을 위반하거나 다른 사람의 권리를 침해하는 목적으로 앱을 사용해서는 안 됩니다.</li>
    </ol>

    <h4>제6조 (결과의 정확성)</h4>
    <p>AI가 만든 번역·주석·답변과, 자동으로 추출한 표·수식에는 틀리거나 빠진 내용이 있을 수 있습니다. 인용하거나 중요한 판단에 쓰기 전에는 반드시 원문과 대조해 확인해 주세요.</p>

    <h4>제7조 (보증의 부인과 책임의 제한)</h4>
    <ol>
      <li>앱은 무료로 “있는 그대로” 제공되며, 관련 법령이 허용하는 범위에서 특정 목적에의 적합성이나 오류가 없음을 보증하지 않습니다.</li>
      <li>제작자는 관련 법령이 허용하는 범위에서 앱 이용으로 생긴 손해를 책임지지 않습니다. 다만 제작자의 고의 또는 중대한 과실로 생긴 손해는 예외입니다.</li>
      <li>중요한 데이터는 이용자가 직접 백업해 주세요.</li>
    </ol>

    <h4>제8조 (변경과 중단)</h4>
    <ol>
      <li>제작자는 앱의 기능을 바꾸거나 개발을 중단할 수 있습니다.</li>
      <li>이 약관을 바꾸면 새 버전의 앱과 GitHub 릴리스 노트로 알립니다. 바뀐 약관에 동의하지 않으면 언제든 앱 사용을 중단할 수 있습니다.</li>
    </ol>

    <h4>제9조 (준거법과 문의)</h4>
    <p>이 약관은 대한민국 법령에 따라 해석됩니다. 문의와 제보는 앱 설정의 <b>버그 제보</b>나 GitHub 저장소로 보내 주세요.</p>
  </div>;
}
