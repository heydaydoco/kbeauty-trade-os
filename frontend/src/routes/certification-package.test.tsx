// 전달 서류 zip 버튼 (S2-4 PR-2 안건 ④) — 내려받기 성공과 서버 거부(409·422) 문구.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PackagePanel } from "./certification-collaboration";
import type { Certification } from "./certifications";

const ROW = { id: 7 } as Certification;

function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    blob: () => Promise.resolve(new Blob(["zip-bytes"])),
    headers: { get: (name: string) => headers[name] ?? null },
  } as unknown as Response;
}

describe("전달 서류 zip 버튼", () => {
  beforeEach(() => {
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:test"),
      revokeObjectURL: vi.fn(),
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("누르면 패키지 API를 GET으로 부르고 서버가 준 파일명으로 저장한다", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        response(200, null, {
          "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent("인증7_전달서류_2026-09-30.zip")}`,
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const clicked: string[] = [];
    const original = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    };
    try {
      render(<PackagePanel row={ROW} />);
      fireEvent.click(screen.getByRole("button", { name: "전달 서류 zip 받기" }));
      await waitFor(() => expect(clicked).toEqual(["인증7_전달서류_2026-09-30.zip"]));
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/certifications/7/package", { method: "GET" });
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      HTMLAnchorElement.prototype.click = original;
    }
  });

  it("실물 유실 409는 서버 문구와 문서 목록을 보인다 — 조용히 빠진 채 내려받지 않는다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          response(409, {
            error: {
              code: "CERTIFICATIONS.PACKAGE.FILES_UNAVAILABLE",
              message: "일부 서류 파일을 읽을 수 없어 전달 묶음을 만들지 않았습니다.",
              detail: {
                documents: [
                  { document_id: 11, reason: "MISSING" },
                  { document_id: 12, reason: "HASH_MISMATCH" },
                ],
              },
              request_id: "r",
            },
          }),
        ),
      ),
    );
    render(<PackagePanel row={ROW} />);
    fireEvent.click(screen.getByRole("button", { name: "전달 서류 zip 받기" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("문서 #11(파일 없음), 문서 #12(내용 불일치)");
    expect(alert).toHaveTextContent("전달 묶음을 만들지 않았습니다");
    expect(screen.getByRole("button", { name: "전달 서류 zip 받기" })).toBeEnabled();
  });

  it("상한 초과 422와 네트워크 오류도 문장으로 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          response(422, {
            error: {
              code: "CERTIFICATIONS.PACKAGE.TOO_LARGE",
              message: "전달 서류가 너무 많거나 커서 한 번에 묶을 수 없습니다.",
              detail: { files: 101 },
              request_id: "r",
            },
          }),
        ),
      ),
    );
    render(<PackagePanel row={ROW} />);
    fireEvent.click(screen.getByRole("button", { name: "전달 서류 zip 받기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("한 번에 묶을 수 없습니다");
  });
});
