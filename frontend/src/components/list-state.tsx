// 목록의 로딩·오류·빈 상태 표준 컴포넌트 (DESIGN.md §18.4).
//
// §18.4가 "빈 목록·로딩 표준 컴포넌트"를 요구하는 이유는 화면마다 다르게 만들면
// 같은 상태가 화면마다 다른 말을 하기 때문이다. 특히 **빈 목록과 오류는 다르다** —
// 둘을 같은 회색 문구로 보여 주면 사용자는 서버가 죽은 것을 "자료 없음"으로 읽는다.

import type { ReactNode } from "react";
import { ApiError } from "../lib/api";

interface ListStateProps {
  isPending: boolean;
  error: unknown;
  isEmpty: boolean;
  /** 비었을 때 무엇을 하면 되는지 — 조치가 없는 안내는 막다른 길이다. */
  emptyHint: ReactNode;
  children: ReactNode;
}

export function ListState({ isPending, error, isEmpty, emptyHint, children }: ListStateProps) {
  if (isPending) {
    return <p className="p-5 text-gray-500">불러오는 중…</p>;
  }

  if (error) {
    // 서버가 준 한국어 문구를 그대로 보여 준다(§18.4) — 화면이 따로 지어내면
    // 사용자가 신고할 때 로그와 말이 맞지 않는다.
    const message =
      error instanceof ApiError
        ? error.message
        : "목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
    const requestId = error instanceof ApiError ? error.requestId : null;
    return (
      <div role="alert" className="p-5 text-signal-red">
        <p>{message}</p>
        {requestId && <p className="mt-1 text-xs text-gray-500">오류 번호: {requestId}</p>}
      </div>
    );
  }

  if (isEmpty) {
    return <p className="p-5 text-gray-500">{emptyHint}</p>;
  }

  return <>{children}</>;
}
