// 문서보관소 화면 — 최소 렌더 + 다운로드 통로 (§4.7 / ADR-0029).
//
// 다운로드 anchor는 권한 검증 엔드포인트(/download) 하나만 가리켜야 한다 —
// 화면이 저장 경로를 만들어 내기 시작하면 단일 통로(조건 4-②)가 무너진다.

import { screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const FILE_DOCUMENT = {
  id: 11,
  owner_type: "SKU",
  owner_id: 1,
  owner_display: "SKU-001",
  document_type: "MSDS",
  document_type_name: "MSDS",
  storage_kind: "FILE",
  original_filename: "수분세럼 MSDS.pdf",
  content_type: "application/pdf",
  size_bytes: 1024,
  sha256: "a".repeat(64),
  url: null,
  issued_on: null,
  valid_until: "2027-01-01",
  retention_until: null,
  note: null,
};

const LINK_DOCUMENT = {
  ...FILE_DOCUMENT,
  id: 12,
  document_type: "ORIGIN_EVIDENCE",
  document_type_name: "원산지 증빙",
  storage_kind: "LINK",
  original_filename: null,
  content_type: null,
  size_bytes: null,
  sha256: null,
  url: "https://example.com/co.pdf",
  issued_on: "2026-03-15",
  retention_until: "2031-03-15",
};

const DOCUMENT_TYPES = [
  { id: 1, code: "MSDS", name_ko: "MSDS", retention_years: null, note: null },
  { id: 2, code: "ORIGIN_EVIDENCE", name_ko: "원산지 증빙", retention_years: 5, note: null },
];

function stubApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string) => {
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
      if (input.includes("/v1/document-types"))
        return Promise.resolve(jsonResponse(page(DOCUMENT_TYPES)));
      if (input.includes("/v1/documents"))
        return Promise.resolve(jsonResponse(page([FILE_DOCUMENT, LINK_DOCUMENT])));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

describe("문서보관소", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("목록·보존기한·등록 폼이 표시된다 (최소 렌더)", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/documents" });

    expect(await screen.findByRole("heading", { name: "문서보관소" })).toBeInTheDocument();
    expect(await screen.findByText("수분세럼 MSDS.pdf")).toBeInTheDocument();
    // 종류명은 셀렉트 옵션에도 있으니 표의 셀로 한정한다(product-detail-bom 선례).
    expect(screen.getByRole("cell", { name: "원산지 증빙" })).toBeInTheDocument();
    expect(screen.getByText("2031-03-15")).toBeInTheDocument(); // 보존기한(발급일+5년)
    expect(screen.getByText("문서 등록")).toBeInTheDocument(); // TRADE는 등록 폼을 본다
  });

  it("FILE은 다운로드 엔드포인트로, LINK는 원 링크로 간다 (단일 통로 — 조건 4-②)", async () => {
    stubApi();
    renderWithProviders(<AppRoutes />, { route: "/documents" });

    const download = await screen.findByRole("link", { name: "수분세럼 MSDS.pdf" });
    expect(download).toHaveAttribute("href", "/api/v1/documents/11/download");

    const external = screen.getByRole("link", { name: "링크 열기" });
    expect(external).toHaveAttribute("href", "https://example.com/co.pdf");
  });
});
