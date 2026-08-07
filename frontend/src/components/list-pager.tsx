// 목록 총계·쪽 이동 표준 컴포넌트 (DESIGN.md §18.4 / 판정 ② 2026-08-06).
//
// ★ "전체 N건" 총계와 쪽 이동은 이 컴포넌트가 전 목록의 유일한 구현이다 —
//   화면별 개별 구현 금지(50건 표시 한계 관찰 항목의 종결 장치). 서버 계약은
//   불변이다(§18.4 기본 50·최대 200) — usePagedList가 page 파라미터를 붙일 뿐이다.

import type { Page } from "../lib/paging";

interface ListPagerProps {
  data: Page<unknown> | undefined;
  page: number;
  onPageChange: (page: number) => void;
  className?: string;
}

export function ListPager({ data, page, onPageChange, className }: ListPagerProps) {
  // 로딩·오류 동안 총계를 지어내지 않는다("전체 0건"은 거짓이다) — 상태는 ListState 몫.
  if (data === undefined) return null;

  const lastPage = Math.max(1, Math.ceil(data.total / data.size));

  return (
    <div className={`flex flex-wrap items-center gap-3 text-sm text-gray-500 ${className ?? ""}`}>
      <span className="cell-nowrap">전체 {data.total}건</span>
      {lastPage > 1 && (
        <span className="flex items-center gap-2">
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange(page - 1)}
            className="rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
          >
            이전
          </button>
          <span className="cell-nowrap num">
            {page}/{lastPage}쪽
          </span>
          <button
            type="button"
            disabled={page >= lastPage}
            onClick={() => onPageChange(page + 1)}
            className="rounded border border-gray-300 px-2 py-1 disabled:opacity-40"
          >
            다음
          </button>
        </span>
      )}
    </div>
  );
}
