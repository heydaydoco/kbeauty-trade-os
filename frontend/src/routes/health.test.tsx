import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HealthPage } from "./health";

// K. 보안·품질 — 프런트 헬스 화면 (정상/이상)

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <HealthPage />
    </QueryClientProvider>,
  );
}

const OK_RESPONSE = {
  status: "ok",
  app_env: "dev",
  version: "0.1.0",
  checks: { db: { status: "ok" }, migration: { status: "ok" } },
  checked_at: "2026-07-24T04:00:00+00:00",
};

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("헬스 화면", () => {
  it("readyz가 정상이면 한국어 '정상'과 환경·버전을 보여준다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(OK_RESPONSE), { status: 200 })),
    );

    renderWithClient();

    // 'API 연결' 항목의 값이 '정상'인지 확인 ('정상'은 여러 곳에 나오므로 위치로 특정)
    const apiConnLabel = await screen.findByText("API 연결");
    expect(apiConnLabel.nextElementSibling).toHaveTextContent("정상");
    expect(screen.getByText("dev")).toBeInTheDocument();
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
    // KST 표기가 붙는다 (UTC 저장·KST 표시)
    expect(screen.getByText(/KST/)).toBeInTheDocument();
  });

  it("백엔드가 다운되면 영문 오류가 아니라 한국어 '이상'과 조치를 보여준다", async () => {
    // fetch가 거부(네트워크 오류) → 비개발자 첫 실행의 가장 흔한 상태
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    renderWithClient();

    expect(await screen.findByText("API 연결: 이상")).toBeInTheDocument();
    expect(screen.getByText(/연결할 수 없습니다/)).toBeInTheDocument();
    expect(screen.getByText(/docker compose ps/)).toBeInTheDocument();
  });

  it("상대경로 /api로 호출한다 (절대 URL 아님)", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify(OK_RESPONSE), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithClient();
    await screen.findByText("0.1.0"); // 렌더 완료 대기 (버전은 유일하게 등장)

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/system/readyz", expect.anything());
  });
});
