// 오더 인테이크 CSV 업로드 화면 (S3-1 PR-14b) — 역할별 노출·파일 사전 검사·멱등 키 규칙·201/422/409 결과·양식 내려받기·접근성.
// 응답 모양은 서버 계약(PROGRESS 'S3-1 PR-14a' PR-14b 인계 계약 2차 갱신본) 그대로. fetch 스텁은 정확 URL·메서드 일치.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { CSV_MAX_BYTES, type CsvImportResult } from "../lib/order-intake-csv";
import { ADMIN } from "../test/approval-fixtures";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { intakeSummary } from "../test/intake-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const IMPORT = "/api/v1/order-intakes/import-csv";
const USERS: GateHandler = ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))];
const ALERTS: GateHandler = ["/v1/alerts/unread-count", "GET", () => jsonResponse({ count: 0 })];
const INBOX: GateHandler = ["/v1/approvals/inbox-count", "GET", () => jsonResponse({ count: 0 })];
const LIST: GateHandler = ["/v1/order-intakes", "GET", () => jsonResponse(page([intakeSummary()]))];

type Respond = () => Response;
const errorBody = (status: number, code: string, message: string, detail: Record<string, unknown> = {}) =>
  jsonResponse({ error: { code, message, detail, request_id: "req-1" } }, status);
const networkDown: Respond = () => {
  throw new TypeError("Failed to fetch");
};

const RESULT: CsvImportResult = {
  original_filename: "po.csv",
  source_sha256: "a".repeat(64),
  row_count: 3,
  group_count: 2,
  line_count: 3,
  unmapped_line_count: 1,
  intakes: [
    { id: 31, version: 1, buyer_partner_id: 3, buyer_name: "ABC Trading", buyer_po_no: "PO-A", currency: "USD", dest_market_code: "US", line_count: 2, unmapped_line_count: 1, total_amount: 25000, total_text: "250.00", first_row_no: 2 },
    { id: 32, version: 1, buyer_partner_id: 3, buyer_name: "ABC Trading", buyer_po_no: "PO-B", currency: "USD", dest_market_code: "US", line_count: 1, unmapped_line_count: 0, total_amount: 1000, total_text: "10.00", first_row_no: 4 },
  ],
};

/** 업로드 응답을 순서대로 돌려준다(마지막 것은 반복). */
function sequence(...responders: Respond[]): Respond {
  let index = 0;
  return () => {
    const respond = responders[Math.min(index, responders.length - 1)] as Respond;
    index += 1;
    return respond();
  };
}

function open(me: unknown = TRADER, upload: Respond = () => jsonResponse(RESULT, 201), extra: GateHandler[] = []) {
  const stub = stubGateFetch(me, [...extra, ["/v1/order-intakes/import-csv", "POST", upload], LIST, USERS, ALERTS, INBOX]);
  renderWithProviders(<AppRoutes />, { route: "/orders/intakes" });
  return stub;
}

async function openPanel() {
  fireEvent.click(await screen.findByRole("button", { name: "CSV 업로드" }));
  return screen.getByRole("region", { name: "CSV 업로드" });
}

function csvFile(name = "po.csv", content = "바이어코드\r\n"): File {
  return new File([content], name, { type: "text/csv" });
}

function pick(file: File) {
  fireEvent.change(screen.getByLabelText("CSV 파일"), { target: { files: [file] } });
}

const uploads = (calls: GateCall[]) => calls.filter((c) => c.url === IMPORT && c.method === "POST");
const keyOf = (call: GateCall | undefined) => call?.headers["Idempotency-Key"];

describe("역할별 노출", () => {
  it.each([
    ["무역", TRADER],
    ["관리자", ADMIN],
  ])("%s에게 'CSV 업로드' 버튼·양식 내려받기가 보인다", async (_label, me) => {
    open(me);
    const panel = await openPanel();
    expect(within(panel).getByRole("button", { name: "표준 양식 내려받기" })).toBeInTheDocument();
    expect(within(panel).getByLabelText("CSV 파일")).toHaveAttribute("accept", ".csv,text/csv");
  });

  it.each([
    ["열람", VIEWER],
    ["인증", { ...TRADER, roles: ["CERT"] }],
    ["물류", { ...TRADER, roles: ["LOGISTICS"] }],
  ])("%s 역할에게는 CSV 업로드·양식 버튼이 없다", async (_label, me) => {
    open(me);
    await screen.findByRole("link", { name: "PO-2026-001" });
    expect(screen.queryByRole("button", { name: "CSV 업로드" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "표준 양식 내려받기" })).not.toBeInTheDocument();
  });

  it("패널 토글은 aria-expanded로 상태를 알린다", async () => {
    open();
    const toggle = await screen.findByRole("button", { name: "CSV 업로드" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "CSV 업로드 닫기" })).toHaveAttribute("aria-expanded", "true");
  });
});

describe("표준 양식 내려받기", () => {
  it("GET template.csv를 부르고 서버 파일명으로 저장한다", async () => {
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined }));
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
    const stub = open(TRADER, undefined, [
      [
        "/v1/order-intakes/template.csv",
        "GET",
        () =>
          ({
            ok: true,
            status: 200,
            blob: () => Promise.resolve(new Blob(["﻿바이어코드"])),
            headers: new Headers({ "Content-Disposition": "attachment; filename*=UTF-8''%EC%96%91%EC%8B%9D.csv" }),
          }) as unknown as Response,
      ],
    ]);
    const panel = await openPanel();
    fireEvent.click(within(panel).getByRole("button", { name: "표준 양식 내려받기" }));
    await waitFor(() => expect(clicked).toEqual(["양식.csv"]));
    expect(stub.calls.some((c) => c.url === "/api/v1/order-intakes/template.csv" && c.method === "GET")).toBe(true);
  });

  it("403이면 래치 — 업로드 조작을 거두고 안내한다", async () => {
    open(TRADER, undefined, [["/v1/order-intakes/template.csv", "GET", () => errorBody(403, "AUTH.FORBIDDEN", "권한이 없습니다.")]]);
    const panel = await openPanel();
    fireEvent.click(within(panel).getByRole("button", { name: "표준 양식 내려받기" }));
    expect(await screen.findByText(/CSV 업로드와 표준 양식은 무역·관리자만/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "CSV 업로드" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("CSV 파일")).not.toBeInTheDocument();
  });
});

describe("파일 사전 검사", () => {
  it("엑셀 파일은 서버에 보내지 않고, 오류를 입력칸에 aria로 연결한다", async () => {
    const stub = open();
    await openPanel();
    pick(csvFile("po.xlsx"));
    const input = screen.getByLabelText("CSV 파일");
    const alert = await screen.findByText(/엑셀 파일\(\.xlsx·\.xls\)은 올릴 수 없습니다/);
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(alert.id);
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(uploads(stub.calls)).toHaveLength(0);
    expect(input).toHaveFocus();
  });

  it("20MB 초과는 보내지 않는다", async () => {
    const stub = open();
    await openPanel();
    const big = csvFile();
    Object.defineProperty(big, "size", { value: CSV_MAX_BYTES + 1 });
    pick(big);
    expect(await screen.findByText(/20MB를 넘습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(uploads(stub.calls)).toHaveLength(0);
  });

  it("파일을 고르지 않고 누르면 안내만 한다", async () => {
    const stub = open();
    await openPanel();
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByText("올릴 CSV 파일을 먼저 골라 주세요.")).toBeInTheDocument();
    expect(uploads(stub.calls)).toHaveLength(0);
  });
});

describe("멱등 키 규칙", () => {
  it("multipart 필드명 file로 보내고, 클릭마다 새 키를 만든다(같은 파일이어도)", async () => {
    const report = () => errorBody(422, "ORDER_INTAKE.FILE.INVALID_ROWS", "고칠 행이 있습니다.", { errors: [], total_errors: 0, omitted_errors: 0, counts_by_code: {} });
    const stub = open(TRADER, report);
    await openPanel();
    const file = csvFile();
    pick(file);
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    await waitFor(() => expect(uploads(stub.calls)).toHaveLength(1));
    await screen.findByText("고칠 행이 있습니다.");
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    await waitFor(() => expect(uploads(stub.calls)).toHaveLength(2));
    const [first, second] = uploads(stub.calls);
    expect(keyOf(first)).toMatch(/^[0-9a-f-]{36}$/);
    expect(keyOf(second)).toMatch(/^[0-9a-f-]{36}$/);
    expect(keyOf(first)).not.toBe(keyOf(second));
    const form = first?.rawBody as FormData;
    expect(form).toBeInstanceOf(FormData);
    expect((form.get("file") as File).name).toBe("po.csv");
    // 422(서버가 결론을 준 오류)에는 같은 키 재시도 버튼이 없다.
    expect(screen.queryByRole("button", { name: "결과 다시 받기" })).not.toBeInTheDocument();
  });

  it("네트워크 오류 → '결과 다시 받기'는 같은 키·같은 파일로 보내고 201을 표시한다", async () => {
    const stub = open(TRADER, sequence(networkDown, () => jsonResponse(RESULT, 201)));
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    fireEvent.click(await screen.findByRole("button", { name: "결과 다시 받기" }));
    expect(await screen.findByText(/업로드 완료/)).toBeInTheDocument();
    const [first, second] = uploads(stub.calls);
    expect(keyOf(second)).toBe(keyOf(first));
    expect(second?.rawBody).not.toBe(first?.rawBody);
    expect(((second?.rawBody as FormData).get("file") as File).name).toBe("po.csv");
  });

  it("504도 응답 유실 — 같은 키 재시도", async () => {
    const stub = open(TRADER, sequence(() => jsonResponse("<html>gateway timeout</html>", 504), () => jsonResponse(RESULT, 201)));
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    fireEvent.click(await screen.findByRole("button", { name: "결과 다시 받기" }));
    await screen.findByText(/업로드 완료/);
    const [first, second] = uploads(stub.calls);
    expect(keyOf(second)).toBe(keyOf(first));
  });

  it("응답 유실 뒤에도 '업로드' 버튼은 새 키다(클릭마다 새 키)", async () => {
    const stub = open(TRADER, sequence(networkDown, () => jsonResponse(RESULT, 201)));
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    await screen.findByRole("button", { name: "결과 다시 받기" });
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    await screen.findByText(/업로드 완료/);
    const [first, second] = uploads(stub.calls);
    expect(keyOf(second)).not.toBe(keyOf(first));
  });

  it("응답 유실 뒤 다른 파일을 고르면 재시도 버튼이 사라진다(같은 키·다른 파일 금지)", async () => {
    open(TRADER, networkDown);
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    await screen.findByRole("button", { name: "결과 다시 받기" });
    pick(csvFile("other.csv"));
    expect(screen.queryByRole("button", { name: "결과 다시 받기" })).not.toBeInTheDocument();
  });

  it("409 LOCK_BUSY → '같은 요청으로 다시 시도'만 같은 키", async () => {
    const stub = open(
      TRADER,
      sequence(() => errorBody(409, "COMMON.CONCURRENCY.LOCK_BUSY", "같은 건을 다른 사용자가 처리 중입니다."), () => jsonResponse(RESULT, 201)),
    );
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByText(/같은 파일을 다른 업로드가 처리하고 있습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "같은 요청으로 다시 시도" }));
    await screen.findByText(/업로드 완료/);
    const [first, second] = uploads(stub.calls);
    expect(keyOf(second)).toBe(keyOf(first));
  });
});

describe("업로드 결과", () => {
  it("201 — 등록 인테이크 표·상세 링크·품번 등록 필요 안내·목록 무효화·결과 영역 포커스", async () => {
    const stub = open();
    await openPanel();
    pick(csvFile());
    await screen.findByRole("link", { name: "PO-2026-001" });
    const listCallsBefore = stub.calls.filter((c) => c.url === "/api/v1/order-intakes").length;
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    const result = await screen.findByRole("region", { name: "업로드 결과" });
    await waitFor(() => expect(result).toHaveFocus());
    const r = within(result);
    expect(r.getByText(/업로드 완료/)).toHaveTextContent("오더 인테이크 2건을 검토 대기로 등록했습니다");
    expect(r.getByRole("link", { name: "PO-A" })).toHaveAttribute("href", "/orders/intakes/31");
    expect(r.getByRole("link", { name: "PO-B" })).toHaveAttribute("href", "/orders/intakes/32");
    expect(r.getByText(/품번 등록 필요/)).toBeInTheDocument();
    expect(r.getByText("250.00 USD")).toBeInTheDocument();
    expect(r.getByText("1 (등록 필요)")).toBeInTheDocument();
    await waitFor(() => expect(stub.calls.filter((c) => c.url === "/api/v1/order-intakes").length).toBeGreaterThan(listCallsBefore));
    // 같은 파일을 실수로 다시 보내지 않게 선택을 비운다.
    expect((screen.getByLabelText("CSV 파일") as HTMLInputElement).value).toBe("");
  });

  it("422 리포트 — 행·열·유형(한국어)·사유 표, 유형별 개수, 파일 수준 오류는 표 위, 영문 code 비노출", async () => {
    open(TRADER, () =>
      errorBody(422, "ORDER_INTAKE.FILE.INVALID_ROWS", "파일에 고쳐야 할 행이 있어 아무것도 등록하지 않았습니다.", {
        errors: [
          { row_no: null, column: null, code: "CSV_SYNTAX", message_ko: "7번째 줄 근처에서 CSV를 읽지 못했습니다." },
          { row_no: 2, column: "바이어코드", code: "BUYER_NOT_REGISTERED", message_ko: "등록되지 않은 바이어 코드입니다." },
          { row_no: 3, column: "통화", code: "UNKNOWN_CURRENCY", message_ko: "알 수 없는 통화입니다." },
        ],
        total_errors: 3,
        omitted_errors: 0,
        counts_by_code: { BUYER_NOT_REGISTERED: 1, CSV_SYNTAX: 1, UNKNOWN_CURRENCY: 1 },
      }),
    );
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    const result = await screen.findByRole("region", { name: "업로드 결과" });
    const r = within(result);
    expect(r.getByText(/등록하지 않았습니다 — 고칠 곳/)).toHaveTextContent("고칠 곳 3건");
    expect(r.getByText(/어떤 PO도 등록되지 않았습니다/)).toBeInTheDocument();
    expect(within(r.getByRole("list", { name: "파일 전체 오류" })).getByText(/7번째 줄 근처/)).toBeInTheDocument();
    const counts = within(r.getByRole("list", { name: "유형별 오류 개수" }));
    expect(counts.getByText(/등록되지 않은 바이어/)).toHaveTextContent("1건");
    expect(counts.getByText(/CSV 문법 오류/)).toBeInTheDocument();
    const rows = within(r.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(3); // 머리글 + 행 오류 2(파일 수준은 표 밖)
    expect(within(rows[1] as HTMLElement).getByText("2")).toBeInTheDocument();
    expect(within(rows[1] as HTMLElement).getByText("바이어코드")).toBeInTheDocument();
    expect(within(rows[2] as HTMLElement).getByText("알 수 없는 통화입니다.")).toBeInTheDocument();
    expect(result).not.toHaveTextContent(/BUYER_NOT_REGISTERED|UNKNOWN_CURRENCY|CSV_SYNTAX/);
    // 상한 미만이면 잘림 고지가 없다.
    expect(r.queryByText(/건만 표시했습니다/)).not.toBeInTheDocument();
  });

  it("상한 200건 — '전체 N건 중 200건' 고지와 외 N건", async () => {
    const errors = Array.from({ length: 200 }, (_, i) => ({ row_no: i + 2, column: "수량", code: "INVALID_FORMAT", message_ko: `수량 형식 오류 ${i}` }));
    open(TRADER, () =>
      errorBody(422, "ORDER_INTAKE.FILE.INVALID_ROWS", "고칠 행이 있습니다.", {
        errors,
        total_errors: 260,
        omitted_errors: 60,
        counts_by_code: { INVALID_FORMAT: 260 },
      }),
    );
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    const result = await screen.findByRole("region", { name: "업로드 결과" });
    const notice = within(result).getByText(/건만 표시했습니다/);
    expect(notice).toHaveTextContent("전체 260건 중 200건만 표시했습니다. 외 60건은");
    expect(within(result).getByText(/형식 오류/, { selector: "li" })).toHaveTextContent("260건");
    expect(within(result).getAllByRole("row")).toHaveLength(201);
  });

  it("409 DUPLICATE_BUYER_PO 리포트 모양(errors 키) — 표로 보인다", async () => {
    open(TRADER, () =>
      errorBody(409, "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", "같은 바이어 PO가 이미 있습니다.", {
        errors: [{ row_no: 5, column: "바이어PO번호", code: "DUPLICATE_BUYER_PO", message_ko: "이미 수주 SO-2026-0001이 같은 PO를 점유 중입니다." }],
        total_errors: 1,
        omitted_errors: 0,
        counts_by_code: { DUPLICATE_BUYER_PO: 1 },
      }),
    );
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    const result = await screen.findByRole("region", { name: "업로드 결과" });
    expect(within(result).getByText(/고칠 곳/)).toHaveTextContent("(이미 등록된 바이어 PO)");
    expect(within(result).getByRole("table")).toHaveTextContent("이미 수주 SO-2026-0001이 같은 PO를 점유 중입니다.");
  });

  it("409 DUPLICATE_BUYER_PO 단건 모양(13a) — 13b 문구와 기존 인테이크 링크", async () => {
    open(TRADER, () => errorBody(409, "TRADE_DOCS.DOCUMENT.DUPLICATE_BUYER_PO", "중복", { intake_id: 44, status: "PENDING" }));
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    const result = await screen.findByRole("region", { name: "업로드 결과" });
    expect(result).toHaveTextContent("이미 등록된 대기 인테이크: #44 (대기)");
    expect(result).toHaveTextContent(/다시 올리면 어느 행의 PO가 겹치는지/);
    expect(within(result).getByRole("link", { name: "기존 인테이크 #44 보기" })).toHaveAttribute("href", "/orders/intakes/44");
    expect(within(result).queryByRole("table")).not.toBeInTheDocument();
  });

  it("409 FILE.DUPLICATE — 검토 대기 안내와 인테이크·목록 링크", async () => {
    open(TRADER, () => errorBody(409, "ORDER_INTAKE.FILE.DUPLICATE", "같은 파일이 이미 검토 대기 중입니다.", { intake_ids: [31, 32] }));
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    const result = await screen.findByRole("region", { name: "업로드 결과" });
    expect(within(result).getByText("같은 파일이 이미 검토 대기 중입니다")).toBeInTheDocument();
    expect(within(result).getByRole("link", { name: "인테이크 #31" })).toHaveAttribute("href", "/orders/intakes/31");
    expect(within(result).getByRole("link", { name: "인테이크 목록 보기" })).toHaveAttribute("href", "/orders/intakes");
    expect(within(result).queryByRole("button", { name: /다시/ })).not.toBeInTheDocument();
  });

  it("헤더 불일치 — 서버 진단 문구와 열 단위 차이 표", async () => {
    open(TRADER, () =>
      errorBody(422, "IMPORTS.FILE.HEADER_MISMATCH", "파일 첫 행(컬럼 제목)이 표준 양식과 다릅니다.", {
        header: "첫 행(컬럼 제목)이 표준 양식과 다릅니다 — 2열: 기대 '바이어PO번호' / 파일 'PO번호'.",
        differences: [{ column_no: 2, expected: "바이어PO번호", actual: "'PO번호'" }],
      }),
    );
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    const result = await screen.findByRole("region", { name: "업로드 결과" });
    expect(result).toHaveTextContent("파일을 읽지 못했습니다");
    expect(result).toHaveTextContent("2열: 기대 '바이어PO번호' / 파일 'PO번호'");
    const table = within(within(result).getByRole("table"));
    expect(table.getByText("바이어PO번호")).toBeInTheDocument();
    expect(table.getByText("'PO번호'")).toBeInTheDocument();
  });

  it("끝 빈 열·인코딩 줄 번호·PO 수 초과는 파일 수준 안내로", async () => {
    const stub = open(
      TRADER,
      sequence(
        () => errorBody(422, "IMPORTS.FILE.HEADER_MISMATCH", "헤더 다름", { header: "첫 행 오른쪽 끝에 빈 열이 2개 붙어 있습니다.", trailing_empty_columns: 2 }),
        () => errorBody(422, "IMPORTS.FILE.ENCODING_INVALID", "인코딩", { file: "3번째 줄에서 UTF-8로도 CP949로도 읽을 수 없는 글자를 만났습니다.", line_no: 3 }),
        () => errorBody(422, "ORDER_INTAKE.FILE.TOO_MANY_GROUPS", "PO 수 초과", { groups: 230, max_groups: 200 }),
      ),
    );
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByText(/빈 열이 2개/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByText(/3번째 줄에서 UTF-8로도/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByText(/230건이고, 한 파일에 200건까지/)).toBeInTheDocument();
    expect(uploads(stub.calls)).toHaveLength(3);
  });

  it("그 밖의 오류는 서버 한국어 문구(api-errors 경로)", async () => {
    open(TRADER, () => errorBody(413, "IMPORTS.FILE.TOO_LARGE", "파일이 너무 큽니다(최대 20MB)."));
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByRole("region", { name: "업로드 결과" })).toHaveTextContent("파일이 너무 큽니다(최대 20MB).");
  });

  it("업로드 403 — 래치", async () => {
    open(TRADER, () => errorBody(403, "AUTH.FORBIDDEN", "권한이 없습니다."));
    await openPanel();
    pick(csvFile());
    fireEvent.click(screen.getByRole("button", { name: "업로드" }));
    expect(await screen.findByText(/CSV 업로드와 표준 양식은 무역·관리자만/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "CSV 업로드" })).not.toBeInTheDocument();
  });
});
