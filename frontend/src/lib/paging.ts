// 목록 응답 봉투와 조회 훅 (DESIGN.md §18.4 "전 목록 페이지네이션 기본 50").
//
// 서버의 Page 봉투를 화면마다 다시 정의하지 않는다. 모양이 갈리면 나중에
// 페이지 이동 UI를 한 번에 붙일 수 없다.

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "./api";

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

/** 서버 기본값과 같은 값. 화면이 임의로 키우면 §18.4의 상한 규율이 흐려진다. */
export const DEFAULT_PAGE_SIZE = 50;

export function usePagedQuery<T>(key: readonly unknown[], path: string, enabled = true) {
  return useQuery({
    queryKey: key,
    queryFn: () => apiFetch<Page<T>>(path),
    enabled,
  });
}
