// 로그인 상태 (ADR-0013 — 세션은 HttpOnly 쿠키라 JS가 토큰을 읽을 수 없다).
//
// ★ 그래서 "로그인했는가"는 저장된 값이 아니라 **서버에 물어본 결과**다.
//   프런트가 로그인 여부를 localStorage에 캐시하면, 서버에서 세션이 폐기된 뒤에도
//   화면만 로그인 상태로 남아 사용자가 빈 화면을 보게 된다.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "./api";

export interface Me {
  id: number;
  email: string;
  display_name: string;
  roles: string[];
}

export const ME_QUERY_KEY = ["me"] as const;

export function useSession() {
  const query = useQuery({
    queryKey: ME_QUERY_KEY,
    queryFn: () => apiFetch<Me>("/v1/auth/me"),
    // 401은 "아직 로그인 안 함"이라는 정상 상태다 — 재시도하지 않는다.
    retry: false,
    staleTime: 60_000,
  });

  const unauthenticated = query.error instanceof ApiError && query.error.status === 401;

  return {
    me: query.data ?? null,
    isPending: query.isPending,
    unauthenticated,
    // 401이 아닌 에러(서버 다운 등)만 화면에 오류로 보여 준다.
    error: unauthenticated ? null : query.error,
  };
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { email: string; password: string }) =>
      apiFetch<Me>("/v1/auth/login", { method: "POST", body: input }),
    onSuccess: (me) => {
      client.setQueryData(ME_QUERY_KEY, me);
    },
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => apiFetch<void>("/v1/auth/logout", { method: "POST" }),
    onSuccess: () => {
      // 로그아웃 뒤에 남은 캐시는 다른 사람의 화면에 남의 자료를 보여 준다.
      client.clear();
    },
  });
}

export function hasRole(me: Me | null, ...roles: string[]): boolean {
  if (!me) return false;
  return me.roles.includes("ADMIN") || roles.some((role) => me.roles.includes(role));
}
