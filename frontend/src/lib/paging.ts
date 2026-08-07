// 목록 응답 봉투와 조회 훅 (DESIGN.md §18.4 "전 목록 페이지네이션 기본 50").
//
// 서버의 Page 봉투를 화면마다 다시 정의하지 않는다. 모양이 갈리면 나중에
// 페이지 이동 UI를 한 번에 붙일 수 없다.
//
// ★ 쪽 이동은 usePagedList + <ListPager> 한 쌍이 전 목록의 유일한 구현이다
//   (판정 ② 2026-08-06 — 화면별 개별 구현 금지). 서버 계약은 불변이다(§18.4
//   기본 50·최대 200) — 프런트가 page 파라미터를 붙일 뿐이다.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { apiFetch } from "./api";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

/** 서버 기본값과 같은 값. 화면이 임의로 키우면 §18.4의 상한 규율이 흐려진다. */
export const DEFAULT_PAGE_SIZE = 50;

/** 쪽 이동 없는 단순 조회 — 드롭다운(?size=200 우회)·부착물 소형 목록용. */
export function usePagedQuery<T>(key: readonly unknown[], path: string, enabled = true) {
  return useQuery({
    queryKey: key,
    queryFn: () => apiFetch<Page<T>>(path),
    enabled,
  });
}

/** 1쪽은 주소를 바꾸지 않는다 — 기존 요청 모양(기본 50)이 그대로 유지된다. */
function withPage(path: string, page: number): string {
  if (page <= 1) return path;
  return path.includes("?") ? `${path}&page=${page}` : `${path}?page=${page}`;
}

/**
 * 표 목록용 조회 — 쪽 상태를 함께 관리한다. <ListPager>가 이 반환값을 소비한다.
 * 쿼리 키에 쪽이 덧붙지만 prefix 무효화(등록·삭제 후 invalidateQueries)는
 * 부분 일치라 그대로 걸린다.
 */
export function usePagedList<T>(key: readonly unknown[], path: string, enabled = true) {
  const [page, setPage] = useState(1);

  // 경로(필터)가 바뀌면 1쪽으로 — 남은 쪽 번호로 새 필터를 조회하면 빈 표가 된다.
  useEffect(() => {
    setPage(1);
  }, [path]);

  const query = useQuery({
    queryKey: [...key, { page }],
    queryFn: () => apiFetch<Page<T>>(withPage(path, page)),
    enabled,
  });

  // 마지막 쪽의 행이 줄어(삭제 등) 쪽 번호가 범위를 벗어나면 실제 마지막 쪽으로.
  const data = query.data;
  useEffect(() => {
    if (data === undefined) return;
    const lastPage = Math.max(1, Math.ceil(data.total / data.size));
    if (page > lastPage) setPage(lastPage);
  }, [data, page]);

  return { ...query, page, setPage };
}
