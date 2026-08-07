// usePagedList 훅 계약 (판정 ② — 공용 목록 계층의 쪽 상태 규칙).
//
// 이 파일이 고정하는 것: ① 경로(필터·선택 대상) 전환 시 같은 렌더에서 1쪽으로
// 복귀한다 — '새 경로+옛 쪽 번호' 낭비 요청 0 (리뷰 확정 발견의 회귀).
// ② 마지막 쪽의 행이 줄어 쪽 번호가 범위를 벗어나면 실제 마지막 쪽으로 보정한다.
// 두 규칙 모두 화면 테스트가 못 잡는 훅 내부 계약이라 여기서 직접 고정한다.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { jsonResponse } from "../test/render";
import { usePagedList } from "./paging";

interface Row {
  id: number;
}

function Harness() {
  const [target, setTarget] = useState("A");
  const list = usePagedList<Row>(["things", target, "rows"], `/v1/things/${target}/rows`);
  return (
    <div>
      <button onClick={() => setTarget("B")}>전환</button>
      <button onClick={() => list.setPage(2)}>2쪽</button>
      <button onClick={() => list.setPage(3)}>3쪽</button>
      <div data-testid="page">{list.page}</div>
      <div data-testid="data">{list.data ? `${list.data.total}@${list.data.page}` : "없음"}</div>
    </div>
  );
}

function renderHarness() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

describe("usePagedList", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("★ 경로 전환은 같은 렌더에서 1쪽 복귀 — '새 경로+옛 쪽 번호' 요청이 나가지 않는다", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const url = String(input);
        calls.push(url);
        const page = url.includes("page=") ? Number(url.split("page=")[1]) : 1;
        return Promise.resolve(
          jsonResponse({ items: [{ id: page }], total: 120, page, size: 50 }),
        );
      }),
    );
    renderHarness();

    await screen.findByText("120@1");
    fireEvent.click(screen.getByText("2쪽"));
    await screen.findByText("120@2");

    const before = calls.length;
    fireEvent.click(screen.getByText("전환"));
    await screen.findByText("120@1");

    // 전환 후 요청은 B의 1쪽 하나뿐이어야 한다 — page=2가 섞이면 회귀다.
    expect(calls.slice(before)).toEqual(["/api/v1/things/B/rows"]);
    expect(screen.getByTestId("page").textContent).toBe("1");
  });

  it("범위를 벗어난 쪽 번호는 실제 마지막 쪽으로 보정된다 (행 축소)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string) => {
        const url = String(input);
        if (url.includes("page=3")) {
          // 3쪽을 요청했지만 그 사이 행이 줄어 총 20건(1쪽뿐)이 된 상황.
          return Promise.resolve(jsonResponse({ items: [], total: 20, page: 3, size: 50 }));
        }
        return Promise.resolve(
          jsonResponse({ items: [{ id: 1 }], total: 120, page: 1, size: 50 }),
        );
      }),
    );
    renderHarness();

    await screen.findByText("120@1");
    fireEvent.click(screen.getByText("3쪽"));

    // 3쪽 응답(총 20건)이 도착하면 마지막 쪽(1쪽)으로 보정된다.
    await screen.findByText("120@1");
    expect(screen.getByTestId("page").textContent).toBe("1");
  });
});
