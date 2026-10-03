// CSV 업로드 오류 분류·사전 검사 단위 시험 (S3-1 PR-14b) — 분기는 code·detail 키 모양, 표시는 한국어.

import { describe, expect, it } from "vitest";
import { ApiError } from "./api";
import { CSV_MAX_BYTES, classifyUploadError, isResponseLost, parseCsvReport, precheckCsvFile, rowCodeLabel } from "./order-intake-csv";

const err = (status: number, code: string, detail: Record<string, unknown> = {}, message = "서버 문구") =>
  new ApiError(status, { code, message, detail, requestId: null });

describe("파일 사전 검사", () => {
  it(".csv(대소문자 무관)·20MiB 이하는 통과", () => {
    expect(precheckCsvFile({ name: "po.CSV", size: 10 })).toBeNull();
    expect(precheckCsvFile({ name: "po.csv", size: CSV_MAX_BYTES })).toBeNull();
  });
  it("엑셀·다른 확장자·빈 파일·20MiB 초과는 거부(한국어 사유)", () => {
    expect(precheckCsvFile({ name: "po.xlsx", size: 10 })).toMatch(/엑셀 파일/);
    expect(precheckCsvFile({ name: "po.txt", size: 10 })).toMatch(/CSV 파일\(\.csv\)만/);
    expect(precheckCsvFile({ name: "po.csv", size: 0 })).toMatch(/빈 파일/);
    expect(precheckCsvFile({ name: "po.csv", size: CSV_MAX_BYTES + 1 })).toMatch(/20MB/);
  });
});

describe("리포트 파싱", () => {
  it("errors 키가 없으면 null — 있으면 모양이 깨진 항목만 건너뛴다", () => {
    expect(parseCsvReport({ intake_id: 3 })).toBeNull();
    const r = parseCsvReport({
      errors: [{ row_no: 2, column: "통화", code: "UNKNOWN_CURRENCY", message_ko: "통화 오류" }, { bad: 1 }],
      total_errors: 3,
      omitted_errors: 1,
      counts_by_code: { UNKNOWN_CURRENCY: 2, REQUIRED: 1, X: "y" },
    });
    expect(r?.errors).toHaveLength(1);
    expect(r?.totalErrors).toBe(3);
    expect(r?.omittedErrors).toBe(1);
    expect(r?.countsByCode).toEqual([
      ["UNKNOWN_CURRENCY", 2],
      ["REQUIRED", 1],
    ]);
  });
  it("모르는 code 라벨은 '기타'(영문 code 비노출)", () => {
    expect(rowCodeLabel("DUPLICATE_BUYER_PO")).toBe("이미 등록된 PO");
    expect(rowCodeLabel("SOMETHING_NEW")).toBe("기타");
  });
});

describe("업로드 오류 분류", () => {
  it("응답 유실 = 네트워크(0)·504만", () => {
    expect(isResponseLost(err(0, "CLIENT.NETWORK.UNREACHABLE"))).toBe(true);
    expect(isResponseLost(err(504, "CLIENT.RESPONSE.UNKNOWN"))).toBe(true);
    expect(isResponseLost(err(502, "CLIENT.RESPONSE.UNKNOWN"))).toBe(false);
    expect(isResponseLost(err(500, "X"))).toBe(false);
    expect(classifyUploadError(err(504, "CLIENT.RESPONSE.UNKNOWN")).kind).toBe("response-lost");
  });
  it("409 DUPLICATE_BUYER_PO — errors 키가 있으면 리포트, 없으면 단건(13a 모양)", () => {
    const report = classifyUploadError(
      err(409, "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", { errors: [], total_errors: 0, omitted_errors: 0, counts_by_code: {} }),
    );
    expect(report.kind).toBe("report");
    const single = classifyUploadError(err(409, "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", { intake_id: 7, status: "PENDING" }));
    expect(single.kind).toBe("duplicate-po");
    expect(single.message).toMatch(/이미 등록된 대기 인테이크: #7 \(대기\)/);
  });
  it("FILE.DUPLICATE는 intake_ids를, 경합으로 비어 있으면 빈 목록", () => {
    const a = classifyUploadError(err(409, "ORDER_INTAKE.FILE.DUPLICATE", { intake_ids: [4, 5] }));
    expect(a).toMatchObject({ kind: "file-duplicate", intakeIds: [4, 5] });
    const b = classifyUploadError(err(409, "ORDER_INTAKE.FILE.DUPLICATE"));
    expect(b).toMatchObject({ kind: "file-duplicate", intakeIds: [] });
  });
  it("헤더 불일치 — 한국어 진단·열 단위 차이", () => {
    const o = classifyUploadError(
      err(422, "IMPORTS.FILE.HEADER_MISMATCH", {
        header: "첫 행이 다릅니다 — 2열",
        differences: [{ column_no: 2, expected: "바이어PO번호", actual: "'PO번호'" }],
      }),
    );
    expect(o).toMatchObject({ kind: "file", notes: ["첫 행이 다릅니다 — 2열"], differences: [{ columnNo: 2, expected: "바이어PO번호", actual: "'PO번호'" }] });
  });
  it("PO 수 초과 — 숫자는 detail에서", () => {
    const o = classifyUploadError(err(422, "ORDER_INTAKE.FILE.TOO_MANY_GROUPS", { groups: 201, max_groups: 200 }));
    expect(o.kind).toBe("file");
    expect(o.kind === "file" && o.notes[0]).toMatch(/201건.*200건/);
  });
  it("LOCK_BUSY·KEY_CONFLICT·403·기타", () => {
    expect(classifyUploadError(err(409, "COMMON.CONCURRENCY.LOCK_BUSY")).kind).toBe("lock-busy");
    expect(classifyUploadError(err(409, "COMMON.IDEMPOTENCY.KEY_CONFLICT")).message).toMatch(/새 요청/);
    expect(classifyUploadError(err(403, "AUTH.FORBIDDEN")).kind).toBe("forbidden");
    expect(classifyUploadError(err(500, "X", {}, "서버 내부 오류입니다.")).message).toBe("서버 내부 오류입니다.");
  });
});
