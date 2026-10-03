// 잘림 고지 공통 컴포넌트 (design-D D6 — 신규 화면부터 추출, 기존 인증 보드 이관은 별도 커밋).
//
// ★ 조용한 잘림 금지(관찰 등재 #3) — 서버가 준 전체 건수보다 적게 보이면 그 사실과 다음 할 일을 말한다.

import type { ReactNode } from "react";

interface TruncationNoticeProps {
  total: number;
  shown: number;
  /** 나머지를 보는 방법(예: '더 보기' 버튼 안내). */
  children?: ReactNode;
  className?: string;
}

export function TruncationNotice({ total, shown, children, className }: TruncationNoticeProps) {
  if (total <= shown) return null;
  return (
    <p role="status" className={`break-keep rounded border border-signal-amber/60 p-2 text-xs ${className ?? ""}`}>
      전체 <span className="num">{total}</span>건 중 <span className="num">{shown}</span>건만 표시했습니다. {children}
    </p>
  );
}
