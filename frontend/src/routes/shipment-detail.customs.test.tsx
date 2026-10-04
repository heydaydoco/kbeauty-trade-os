// 선적 상세 — 통관 기록 섹션(S3-2 PR-4b, 그룹 A·H·K): 목록(미수리 배지·세율/세액 칸 0)·추가 본문(구분 고정·빈칸 키 생략·멱등 키)·
// 신고번호 사전 검사·날짜 상한(서버 KST 오늘)·정정 사유 규칙(번호·신고일·기존 수리일 = 필수 / 첫 수리일 = 불요)·삭제(version·사유 본문)·
// 409 중복 서버 문구·409 version → 다시 불러오기·버튼 = EDIT_CUSTOMS만.

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { stubGateFetch, type GateCall, type GateHandler } from "../test/gate-fixtures";
import { customsRecord, exportBoard } from "../test/milestone-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";
import { soDetail, SO_LINE } from "../test/so-fixtures";
import { apiErrorResponse, shipmentDetail } from "../test/shipment-fixtures";
import type { CustomsRecord } from "../lib/milestone";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const SH = "/v1/shipments/31";
const ACTIONS = ["RELEASE_ORDER", "CANCEL", "EDIT_LINES", "EDIT_COUNTRIES", "EDIT_META", "EDIT_PARTIES", "EDIT_MILESTONES", "PLAN_DRAFT", "EDIT_CUSTOMS"];
const server = { records: [] as CustomsRecord[], detail: shipmentDetail({ allowed_actions: ACTIONS, milestones: exportBoard() }) };

function open(records: CustomsRecord[], extra: GateHandler[] = [], me: unknown = TRADER, actions = ACTIONS) {
  server.records = records;
  server.detail = shipmentDetail({
    allowed_actions: actions,
    milestones: exportBoard(),
    customs_summary: {
      live_count: records.length,
      pending_count: records.filter((r) => r.accepted_on === null).length,
      latest_accepted_on: null,
    },
  });
  const stub = stubGateFetch(me, [
    ...extra,
    [`${SH}/status-log`, "GET", () => jsonResponse(page([]))],
    ["/v1/document-flow/SHIPMENT/31", "GET", () => jsonResponse({ root_kind: "SALES_ORDER", root_id: 9, nodes: [] })],
    ["/v1/users/lookup?size=200", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
    ["/v1/sales-orders/9", "GET", () => jsonResponse(soDetail({ status: "IN_SHIPMENT", lines: [{ ...SO_LINE, shipment_open_quantity: 2 }] }))],
    [`${SH}/customs-records`, "GET", () => jsonResponse(page(server.records))],
    [`${SH}/milestone-changes`, "GET", () => jsonResponse(page([]))],
    [SH, "GET", () => jsonResponse(server.detail)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/shipments/31" });
  return stub;
}

const section = () => screen.getByRole("region", { name: "통관 기록" });
const dialog = () => screen.getByRole("dialog");
const sent = (calls: GateCall[], path: string, method: string) => calls.filter((c) => c.method === method && c.url === `/api${path}`);

describe("통관 기록 — 목록", () => {
  it("구분·신고번호·신고일·수리일(없으면 '미수리' 배지)·관세사·메모, 세율·세액·HS 칸 없음, 사실 기록 안내", async () => {
    open([customsRecord(), customsRecord({ id: 302, declaration_no: "AB-1002", accepted_on: "2026-10-02", customs_broker: null, note: "검사 지정" })]);
    const region = await screen.findByRole("region", { name: "통관 기록" });
    const pending = (await within(region).findByText("AB-1001")).closest("tr") as HTMLElement;
    expect(within(pending).getByText("수출신고")).toBeInTheDocument();
    expect(within(pending).getByText("미수리")).toBeInTheDocument();
    expect(within(pending).getByText("서울관세")).toBeInTheDocument();
    const cleared = within(region).getByText("AB-1002").closest("tr") as HTMLElement;
    expect(within(cleared).getByText("2026-10-02")).toBeInTheDocument();
    expect(within(cleared).queryByText("미수리")).not.toBeInTheDocument();
    expect(within(region).queryByRole("columnheader", { name: /세율|세액|HS|과세/ })).not.toBeInTheDocument();
    expect(within(region).getByText(/세율·세액·HS 판정은 기록하지 않습니다/)).toBeInTheDocument();
    expect(within(region).getByText("살아 있는 기록 2건")).toBeInTheDocument();
    expect(within(region).getByText("미수리 1건")).toBeInTheDocument();
  });

  it("EDIT_CUSTOMS가 없으면 추가·정정·삭제 버튼 0(조회 역할)", async () => {
    open([customsRecord()], [], VIEWER, []);
    const region = await screen.findByRole("region", { name: "통관 기록" });
    expect(await within(region).findByText("AB-1001")).toBeInTheDocument();
    expect(within(region).queryAllByRole("button")).toHaveLength(0);
  });
});

describe("통관 기록 — 추가", () => {
  it("구분은 선적 구분에서 고정, 빈칸 필드는 키 자체를 보내지 않는다(멱등 키 1개)", async () => {
    const stub = open([], [[`${SH}/customs-records`, "POST", () => jsonResponse(customsRecord(), 201)]]);
    await screen.findByRole("region", { name: "통관 기록" });
    fireEvent.click(within(section()).getByRole("button", { name: "통관 기록 추가" }));
    const box = dialog();
    expect(within(box).getByRole("heading", { name: "통관 기록 추가 — 수출신고" })).toBeInTheDocument();
    expect(within(box).queryByLabelText(/구분/)).not.toBeInTheDocument(); // 고르는 칸 없음(고정)
    fireEvent.change(within(box).getByLabelText("신고번호 (필수)"), { target: { value: " ab-1001 " } });
    fireEvent.change(within(box).getByLabelText("신고일 (필수)"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(box).getByRole("button", { name: "추가" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const [call] = sent(stub.calls, `${SH}/customs-records`, "POST");
    expect(call?.body).toEqual({ declaration_kind: "EXPORT", declaration_no: "ab-1001", declared_on: "2026-10-01" });
    expect(call?.headers["Idempotency-Key"]).toMatch(/^key-/);
  });

  it("신고번호 규약 밖(공백·한글·41자)·신고일 > 서버 KST 오늘·수리일 < 신고일 → 사전 차단(요청 0)", async () => {
    const stub = open([]);
    await screen.findByRole("region", { name: "통관 기록" });
    fireEvent.click(within(section()).getByRole("button", { name: "통관 기록 추가" }));
    const box = dialog();
    const add = within(box).getByRole("button", { name: "추가" });
    fireEvent.change(within(box).getByLabelText("신고일 (필수)"), { target: { value: "2026-10-01" } });
    for (const bad of ["AB 1001", "통관-1", "A".repeat(41), "-AB"]) {
      fireEvent.change(within(box).getByLabelText("신고번호 (필수)"), { target: { value: bad } });
      expect(add, bad).toBeDisabled();
    }
    expect(within(box).getByText(/영문·숫자·하이픈\(-\)·슬래시\(\/\)로 40자 이내/)).toBeInTheDocument();
    fireEvent.change(within(box).getByLabelText("신고번호 (필수)"), { target: { value: "AB-1001" } });
    expect(add).toBeEnabled();
    fireEvent.change(within(box).getByLabelText("신고일 (필수)"), { target: { value: "2026-10-05" } }); // 오늘(2026-10-04) 다음 날
    expect(add).toBeDisabled();
    expect(within(box).getByText(/신고일은 2000-01-01 ~ 2026-10-04\(오늘\) 사이/)).toBeInTheDocument();
    fireEvent.change(within(box).getByLabelText("신고일 (필수)"), { target: { value: "2026-10-03" } });
    fireEvent.change(within(box).getByLabelText(/수리일/), { target: { value: "2026-10-02" } });
    expect(add).toBeDisabled();
    expect(within(box).getByText("수리일이 신고일보다 앞설 수 없습니다.")).toBeInTheDocument();
    expect(within(box).getByText(/적재기한\(수리일\+30일\)이 다시 계산됩니다/)).toBeInTheDocument();
    fireEvent.click(add);
    expect(sent(stub.calls, `${SH}/customs-records`, "POST")).toHaveLength(0);
  });

  it("409 중복(같은 구분·번호) → 서버 문구 그대로, 대화상자 유지", async () => {
    open(
      [],
      [
        [
          `${SH}/customs-records`,
          "POST",
          () => jsonResponse(apiErrorResponse("SHIPMENTS.CUSTOMS.DECLARATION_DUPLICATE", "같은 구분·신고번호의 통관 기록이 이미 있습니다."), 409),
        ],
      ],
    );
    await screen.findByRole("region", { name: "통관 기록" });
    fireEvent.click(within(section()).getByRole("button", { name: "통관 기록 추가" }));
    fireEvent.change(within(dialog()).getByLabelText("신고번호 (필수)"), { target: { value: "AB-1001" } });
    fireEvent.change(within(dialog()).getByLabelText("신고일 (필수)"), { target: { value: "2026-10-01" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "추가" }));
    expect(await within(dialog()).findByText("같은 구분·신고번호의 통관 기록이 이미 있습니다.")).toBeInTheDocument();
  });
});

describe("통관 기록 — 정정·삭제", () => {
  it("미수리 기록에 수리일 첫 입력 = 사유 칸 없음, 본문 {version, accepted_on}만", async () => {
    const stub = open([customsRecord({ version: 4 })], [[`${SH}/customs-records/301`, "PATCH", () => jsonResponse(customsRecord({ accepted_on: "2026-10-03", version: 5 }))]]);
    const region = await screen.findByRole("region", { name: "통관 기록" });
    fireEvent.click(await within(region).findByRole("button", { name: "정정" }));
    const box = dialog();
    expect(within(box).getByRole("button", { name: "정정 저장" })).toBeDisabled(); // 바뀐 것 없음
    fireEvent.change(within(box).getByLabelText(/수리일/), { target: { value: "2026-10-03" } });
    expect(within(box).queryByLabelText("정정 사유 (필수)")).not.toBeInTheDocument();
    fireEvent.click(within(box).getByRole("button", { name: "정정 저장" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sent(stub.calls, `${SH}/customs-records/301`, "PATCH")[0]?.body).toEqual({ version: 4, accepted_on: "2026-10-03" });
  });

  it("기존 수리일 변경·지우기 / 신고번호 변경 = 정정 사유 필수(빈칸 제출 불가)", async () => {
    const stub = open(
      [customsRecord({ accepted_on: "2026-10-02", version: 2 })],
      [[`${SH}/customs-records/301`, "PATCH", () => jsonResponse(customsRecord({ accepted_on: null, version: 3 }))]],
    );
    const region = await screen.findByRole("region", { name: "통관 기록" });
    fireEvent.click(await within(region).findByRole("button", { name: "정정" }));
    const box = dialog();
    fireEvent.change(within(box).getByLabelText(/수리일/), { target: { value: "" } });
    const save = within(box).getByRole("button", { name: "정정 저장" });
    expect(within(box).getByLabelText("정정 사유 (필수)")).toBeInTheDocument();
    expect(save).toBeDisabled();
    fireEvent.change(within(box).getByLabelText("정정 사유 (필수)"), { target: { value: "  " } });
    expect(save).toBeDisabled();
    fireEvent.change(within(box).getByLabelText("정정 사유 (필수)"), { target: { value: "수리 취소 통보" } });
    fireEvent.click(save);
    await waitFor(() => expect(sent(stub.calls, `${SH}/customs-records/301`, "PATCH")).toHaveLength(1));
    expect(sent(stub.calls, `${SH}/customs-records/301`, "PATCH")[0]?.body).toEqual({ version: 2, accepted_on: null, reason: "수리 취소 통보" });
  });

  it("정정 409 version → '최신 내용 불러오기'로 닫고 목록·상세를 다시 받는다", async () => {
    const stub = open(
      [customsRecord()],
      [[`${SH}/customs-records/301`, "PATCH", () => jsonResponse(apiErrorResponse("COMMON.CONCURRENCY.VERSION_CONFLICT", "충돌"), 409)]],
    );
    const region = await screen.findByRole("region", { name: "통관 기록" });
    fireEvent.click(await within(region).findByRole("button", { name: "정정" }));
    fireEvent.change(within(dialog()).getByLabelText("메모 (선택)"), { target: { value: "메모" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "정정 저장" }));
    const before = sent(stub.calls, `${SH}/customs-records`, "GET").length;
    fireEvent.click(await within(dialog()).findByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(sent(stub.calls, `${SH}/customs-records`, "GET").length).toBeGreaterThan(before));
  });

  it("삭제 = 사유 필수 확인창 → DELETE 본문 {version, reason}, 성공 뒤 목록·상세 재조회", async () => {
    const stub = open([customsRecord({ version: 6 })], [[`${SH}/customs-records/301`, "DELETE", () => ({ ok: true, status: 204, json: () => Promise.reject(new Error("no body")) }) as Response]]);
    const region = await screen.findByRole("region", { name: "통관 기록" });
    fireEvent.click(await within(region).findByRole("button", { name: "삭제" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("AB-1001");
    expect(within(dialog()).getByRole("button", { name: "삭제" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("삭제 사유 (필수)"), { target: { value: "중복 입력" } });
    fireEvent.click(within(dialog()).getByRole("button", { name: "삭제" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const [call] = sent(stub.calls, `${SH}/customs-records/301`, "DELETE");
    expect(call?.body).toEqual({ version: 6, reason: "중복 입력" });
    await waitFor(() => expect(sent(stub.calls, SH, "GET").length).toBeGreaterThan(1));
  });
});
