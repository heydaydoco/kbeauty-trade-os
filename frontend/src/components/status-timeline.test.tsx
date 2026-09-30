// 상태 이력 타임라인 — 서버 status-log를 그대로 그린다(사유·행위자·자동 표식·KST).

import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { quotationStatusLabel } from "../lib/doc-status";
import { LOG, stubFetch } from "../test/qt-fixtures";
import { TRADER, jsonResponse, renderWithProviders } from "../test/render";
import { StatusTimeline } from "./status-timeline";

afterEach(() => vi.unstubAllGlobals());

function renderTimeline() {
  return renderWithProviders(
    <StatusTimeline basePath="/v1/quotations/7" queryKey={["x"]} statusLabel={quotationStatusLabel} />,
  );
}

describe("상태 이력 타임라인", () => {
  it("전이·사유·행위자·자동 표식·KST 시각을 최신순으로 보인다", async () => {
    const { calls } = stubFetch(TRADER, [["/status-log", "GET", () => jsonResponse(LOG)]]);
    renderTimeline();

    expect(await screen.findByText("발행 → 취소")).toBeInTheDocument();
    expect(screen.getByText("사유: 바이어 요청")).toBeInTheDocument();
    expect(screen.getByText("초안 → 발행")).toBeInTheDocument();
    expect(screen.getByText("초안 (작성)")).toBeInTheDocument();
    expect(screen.getByText("자동(시스템)")).toBeInTheDocument();
    expect(screen.getAllByText("무역 담당")).toHaveLength(2);
    expect(screen.getByText(/2026\. 09\. 30\. 10:00 \(KST\)/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("/v1/quotations/7/status-log"))).toBe(true);
    // 전체 건수·순서
    expect(screen.getByText("전체 3건")).toBeInTheDocument();
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("발행 → 취소");
    expect(items[2]).toHaveTextContent("초안 (작성)");
  });

  it("빈 이력은 안내 문구를 보인다", async () => {
    stubFetch(TRADER, [
      ["/status-log", "GET", () => jsonResponse({ items: [], total: 0, page: 1, size: 50 })],
    ]);
    renderTimeline();
    expect(await screen.findByText("상태 변경 이력이 없습니다.")).toBeInTheDocument();
  });

  it("조회 실패는 빈 이력과 구분해 오류로 보인다", async () => {
    stubFetch(TRADER, [
      [
        "/status-log",
        "GET",
        () => jsonResponse({ error: { code: "X", message: "이력을 읽을 수 없습니다." } }, 500),
      ],
    ]);
    renderTimeline();
    expect(await screen.findByRole("alert")).toHaveTextContent("이력을 읽을 수 없습니다.");
  });
});
