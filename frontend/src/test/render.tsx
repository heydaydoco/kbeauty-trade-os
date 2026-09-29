// 테스트 렌더 헬퍼 — 화면이 필요로 하는 껍데기(쿼리 클라이언트·라우터)를 한 곳에서.
//
// 화면마다 직접 감싸면 재시도 설정이 갈려서 어떤 테스트는 401을 3번 재시도하며
// 느려지고, 어떤 테스트는 라우터가 없어 <Link>에서 터진다.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router";

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", client: injected }: { route?: string; client?: QueryClient } = {},
) {
  // `client`는 운영 캐시 설정(staleTime 30초 등)을 그대로 재현해야 하는 테스트용이다 —
  // 기본 클라이언트(staleTime 0)로는 "돌아왔을 때 옛 값이 남는" 류의 결함이 가려진다.
  const client =
    injected ??
    new QueryClient({
      // 테스트에서 재시도는 실패를 느리게 만들 뿐이다.
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

export function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

export function page<T>(items: T[]): { items: T[]; total: number; page: number; size: number } {
  return { items, total: items.length, page: 1, size: 50 };
}

export const TRADER = {
  id: 1,
  email: "trade@example.com",
  display_name: "무역 담당",
  roles: ["TRADE"],
};

export const VIEWER = { ...TRADER, roles: ["VIEWER"] };
