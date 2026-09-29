// 인증 상세의 대행 협업 패널 — 정보 편집·통신 기록 (S2-4 PR-1 안건 ①·⑪).
//
// 고정하는 계약: ① 편집 UI는 인증+관리자만(서버가 최종 차단) ② 바뀐 필드만 PATCH로 보낸다(낙관 잠금
// version 동반) ③ 처리방식 AGENCY ⇔ 대행사 지정 — 화면이 같은 규칙으로 선택지를 좁힌다 ④ 만료일 정정은
// 승인·만료임박·만료에서만 열린다 ⑤ 통신 기록은 발송 없이 기록만: 등록·완료·재개·삭제 요청의 모양
// ⑥ 오간 날 기본값은 KST 오늘이다(브라우저 시간대에 의존하지 않는다).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { TRADER, jsonResponse, page, renderWithProviders } from "../test/render";

const CERT_USER = { id: 2, email: "cert@example.com", display_name: "인증 담당", roles: ["CERT"] };

// KST 2026-09-30 12:00 — 모든 날짜 기본값 검증의 기준일(Date만 고정하고 타이머는 그대로 둔다).
const NOW = new Date("2026-09-30T03:00:00Z");
const TODAY = "2026-09-30";

const BASE_ROW = {
  id: 1,
  template_id: 10,
  market_code: "US",
  template_name: "MoCRA 제품 리스팅",
  requirement_type: "REGISTRATION",
  target_type: "SKU",
  target_id: 5,
  target_label: "수분 세럼 30ml",
  status: "PREPARING",
  validity_months: null,
  renewal_cycle_months: null,
  renewal_lead_days: 90,
  source_url: "https://example.test/rule",
  last_verified_on: "2026-08-01",
  cert_number: null,
  applied_on: null,
  approved_on: null,
  valid_from: null,
  expires_on: null,
  assignee_id: null,
  assignee_name: null,
  note: null,
  version: 3,
  handling_mode: "DIRECT",
  action_owner: "INTERNAL",
  action_owner_changed_on: null,
  agency_partner_id: null,
  agency_partner_name: null,
  is_overdue: false,
  overdue_days: null,
};

const AGENCY_ROW = {
  ...BASE_ROW,
  handling_mode: "AGENCY",
  action_owner: "AGENCY",
  action_owner_changed_on: "2026-09-20",
  agency_partner_id: 1,
  agency_partner_name: "가 대행사",
};

const AGENCY = {
  id: 1,
  partner_code: "AGY-1",
  name_ko: "가 대행사",
  type_codes: ["CERT_AGENCY"],
  credit_limit_amount: null,
  credit_limit_currency: null,
  dg_capable: null,
  strengths: null,
  weaknesses: null,
  note: null,
};
const SUPPLIER = { ...AGENCY, id: 5, partner_code: "SUP-1", name_ko: "공급사", type_codes: ["SUPPLIER"] };

const LOG_OPEN = {
  id: 11,
  subject_type: "CERTIFICATION",
  subject_id: 1,
  subject_label: "#1 · MoCRA 제품 리스팅",
  partner_id: 1,
  partner_name: "가 대행사",
  occurred_on: "2026-09-25",
  summary: "보완 서류 목록 수신",
  next_action: "샘플 재발송",
  next_action_due: "2026-09-28",
  next_action_done_on: null,
  follow_up_open: true,
  follow_up_overdue: true,
  attachment_count: 2,
  created_at: "2026-09-26T01:00:00+00:00",
  version: 2,
};
const LOG_DONE = {
  ...LOG_OPEN,
  id: 12,
  summary: "회신 완료 건",
  next_action: "서류 확인",
  next_action_due: "2026-09-27",
  next_action_done_on: "2026-09-27",
  follow_up_open: false,
  follow_up_overdue: false,
  attachment_count: 0,
  version: 5,
};

interface Recorded {
  url: string;
  method: string;
  body: unknown;
}

function stubApi(me: unknown, row: unknown, calls: Recorded[] = [], logs: unknown[] = [LOG_OPEN, LOG_DONE]) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        url: input,
        method,
        body: init?.body && typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      });
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(me));
      if (input.includes("/v1/comm-logs")) {
        if (method === "POST") return Promise.resolve(jsonResponse(LOG_OPEN, 201));
        if (method === "PATCH") return Promise.resolve(jsonResponse({ ...LOG_OPEN, version: 3 }));
        if (method === "DELETE") return Promise.resolve({ ok: true, status: 204 } as Response);
        return Promise.resolve(jsonResponse(page(logs)));
      }
      if (input.includes("/v1/documents/links") && method === "POST") {
        return Promise.resolve(jsonResponse({ id: 99 }, 201));
      }
      if (input.includes("/v1/documents?owner_type=COMM_LOG")) {
        return Promise.resolve(
          jsonResponse(
            page([
              {
                id: 501,
                owner_type: "COMM_LOG",
                owner_id: 11,
                owner_display: "통신 기록 #11",
                document_type: "CERTIFICATE",
                document_type_name: "인증서",
                storage_kind: "LINK",
                original_filename: null,
                url: "https://example.com/reply.pdf",
              },
            ]),
          ),
        );
      }
      if (input.includes("/v1/document-types")) {
        return Promise.resolve(
          jsonResponse(page([{ id: 1, code: "CERTIFICATE", name_ko: "인증서", retention_years: null, note: null }])),
        );
      }
      if (input.includes("/v1/partners")) return Promise.resolve(jsonResponse(page([AGENCY, SUPPLIER])));
      if (/\/v1\/certifications\/1$/.test(input) && method === "PATCH") {
        return Promise.resolve(jsonResponse({ ...(row as object), version: 4 }));
      }
      if (/\/v1\/certifications\/1$/.test(input)) return Promise.resolve(jsonResponse(row));
      if (input.includes("/status-log")) return Promise.resolve(jsonResponse(page([])));
      if (input.includes("/tasks")) return Promise.resolve(jsonResponse(page([])));
      if (input.includes("/prerequisites")) return Promise.resolve(jsonResponse(page([])));
      if (input.includes("/v1/certifications")) return Promise.resolve(jsonResponse(page([row])));
      return Promise.resolve(jsonResponse(page([])));
    }),
  );
}

async function openDetail(): Promise<void> {
  renderWithProviders(<AppRoutes />, { route: "/certifications?id=1" });
  await screen.findByRole("heading", { name: /MoCRA 제품 리스팅 — 수분 세럼 30ml/ });
}

function patchCalls(calls: Recorded[]): Recorded[] {
  return calls.filter((call) => call.method === "PATCH" && /\/v1\/certifications\/1$/.test(call.url));
}

describe("인증 상세 — 대행 협업 표시·편집", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
    vi.useFakeTimers({ toFake: ["Date"], now: NOW }); // 타이머는 진짜 — react-query·waitFor가 그대로 돈다
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("상세 머리에 처리방식·대행사·공이 있는 곳·넘어온 날이 표시된다", async () => {
    stubApi(TRADER, AGENCY_ROW);
    await openDetail();

    expect(
      await screen.findByText(/처리방식 대행\(가 대행사\) · 공이 있는 곳 대행사\(2026-09-20부터\)/),
    ).toBeInTheDocument();
  });

  it("직접 처리·공 기록 없음은 넘어온 날 표기를 생략한다", async () => {
    stubApi(TRADER, BASE_ROW);
    await openDetail();

    expect(await screen.findByText(/처리방식 직접 · 공이 있는 곳 사내$/)).toBeInTheDocument();
  });

  it("편집 폼은 인증+관리자에게만 보인다 (무역 역할에는 없다 — 서버가 최종 차단)", async () => {
    stubApi(TRADER, BASE_ROW);
    await openDetail();

    await screen.findByText(/처리방식 직접/);
    expect(screen.queryByRole("form", { name: "인증 정보 편집" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "통신 기록 등록" })).not.toBeInTheDocument();
  });

  it("만료일 정정은 승인·만료임박·만료 상태에서만 열린다", async () => {
    stubApi(CERT_USER, BASE_ROW);
    await openDetail();
    const locked = await screen.findByRole("form", { name: "인증 정보 편집" });
    expect(within(locked).getByLabelText("만료일 정정")).toBeDisabled();
    expect(within(locked).getByText(/승인 이후\(승인·만료임박·만료\)에만 정정/)).toBeInTheDocument();
  });

  it("승인 상태에서는 만료일 정정이 열리고, 바뀐 만료일만 낙관 잠금 version과 함께 보낸다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, { ...BASE_ROW, status: "APPROVED", expires_on: "2027-03-31", cert_number: "N-1" }, calls);
    await openDetail();

    const form = await screen.findByRole("form", { name: "인증 정보 편집" });
    const expires = within(form).getByLabelText("만료일 정정");
    expect(expires).not.toBeDisabled();
    expect(expires).toHaveValue("2027-03-31");
    fireEvent.change(expires, { target: { value: "2027-04-30" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(patchCalls(calls)).toHaveLength(1));
    expect(patchCalls(calls)[0]?.body).toEqual({ version: 3, expires_on: "2027-04-30" });
  });

  it("아무것도 바꾸지 않으면 저장 버튼이 잠겨 있다", async () => {
    stubApi(CERT_USER, BASE_ROW);
    await openDetail();

    const form = await screen.findByRole("form", { name: "인증 정보 편집" });
    expect(within(form).getByRole("button", { name: "저장" })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("인증번호"), { target: { value: "A-1" } });
    expect(within(form).getByRole("button", { name: "저장" })).not.toBeDisabled();
  });

  it("대행으로 바꾸면 대행사(인증대행 유형만)를 골라야 저장된다 — 처리방식·대행사가 함께 간다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, BASE_ROW, calls);
    await openDetail();

    const form = await screen.findByRole("form", { name: "인증 정보 편집" });
    expect(within(form).getByLabelText("대행사")).toBeDisabled(); // 직접 처리일 땐 지정 불가
    fireEvent.change(within(form).getByLabelText("처리방식"), { target: { value: "AGENCY" } });
    await waitFor(() => expect(within(form).getByRole("option", { name: "가 대행사" })).toBeInTheDocument());
    expect(within(form).queryByRole("option", { name: "공급사" })).not.toBeInTheDocument();
    // 대행사를 고르기 전에는 저장할 수 없다(AGENCY ⇔ 대행사 지정).
    expect(within(form).getByRole("button", { name: "저장" })).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("대행사"), { target: { value: "1" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(patchCalls(calls)).toHaveLength(1));
    expect(patchCalls(calls)[0]?.body).toEqual({
      version: 3,
      handling_mode: "AGENCY",
      agency_partner_id: 1,
    });
  });

  it("직접 처리일 때 '대행사' 주체는 고를 수 없다", async () => {
    stubApi(CERT_USER, BASE_ROW);
    await openDetail();

    const form = await screen.findByRole("form", { name: "인증 정보 편집" });
    expect(within(form).getByRole("option", { name: "대행사" })).toBeDisabled();
    expect(within(form).getByRole("option", { name: "기관" })).not.toBeDisabled();
  });

  it("대행 → 직접으로 돌아가면 대행사 지정을 풀고 공이 대행사에 있었다면 사내로 되돌려 함께 보낸다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, AGENCY_ROW, calls);
    await openDetail();

    const form = await screen.findByRole("form", { name: "인증 정보 편집" });
    expect(within(form).getByLabelText("처리방식")).toHaveValue("AGENCY");
    expect(within(form).getByLabelText("현재 공이 있는 곳")).toHaveValue("AGENCY");
    fireEvent.change(within(form).getByLabelText("처리방식"), { target: { value: "DIRECT" } });
    expect(within(form).getByLabelText("현재 공이 있는 곳")).toHaveValue("INTERNAL");
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(patchCalls(calls)).toHaveLength(1));
    expect(patchCalls(calls)[0]?.body).toEqual({
      version: 3,
      handling_mode: "DIRECT",
      agency_partner_id: null,
      action_owner: "INTERNAL",
    });
  });

  it("공이 넘어간 날은 입력했을 때만 보낸다 — 오늘 이후는 고를 수 없다(max=KST 오늘)", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, BASE_ROW, calls);
    await openDetail();

    const form = await screen.findByRole("form", { name: "인증 정보 편집" });
    const date = within(form).getByLabelText("공이 넘어간 날 (선택)");
    expect(date).toHaveAttribute("max", TODAY);
    fireEvent.change(within(form).getByLabelText("현재 공이 있는 곳"), { target: { value: "AUTHORITY" } });
    fireEvent.change(date, { target: { value: "2026-09-21" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));

    await waitFor(() => expect(patchCalls(calls)).toHaveLength(1));
    expect(patchCalls(calls)[0]?.body).toEqual({
      version: 3,
      action_owner: "AUTHORITY",
      action_owner_changed_on: "2026-09-21",
    });
  });

  it("서버가 거절하면 필드 문구를 그대로 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(CERT_USER));
        if (/\/v1\/certifications\/1$/.test(input) && init?.method === "PATCH") {
          return Promise.resolve(
            jsonResponse(
              {
                error: {
                  code: "COMMON.VALIDATION.INVALID_FIELD",
                  message: "입력값을 확인해 주세요.",
                  detail: { agency_partner_id: "인증대행 유형이 아닌 거래처입니다." },
                  request_id: null,
                },
              },
              422,
            ),
          );
        }
        if (/\/v1\/certifications\/1$/.test(input)) return Promise.resolve(jsonResponse(BASE_ROW));
        if (input.includes("/v1/partners")) return Promise.resolve(jsonResponse(page([AGENCY])));
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    await openDetail();

    const form = await screen.findByRole("form", { name: "인증 정보 편집" });
    fireEvent.change(within(form).getByLabelText("인증번호"), { target: { value: "A-1" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));

    expect(await within(form).findByRole("alert")).toHaveTextContent(
      "인증대행 유형이 아닌 거래처입니다.",
    );
  });
});

describe("인증 상세 — 통신 기록", () => {
  beforeEach(() => {
    vi.stubGlobal("crypto", { randomUUID: () => "test-key" });
    vi.useFakeTimers({ toFake: ["Date"], now: NOW });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("기록 목록 — 요지·상대·다음 액션·기한 지남/완료 배지·첨부 건수가 보인다", async () => {
    stubApi(TRADER, AGENCY_ROW);
    await openDetail();

    expect(await screen.findByText("보완 서류 목록 수신")).toBeInTheDocument();
    expect(screen.getByText("기한 지남")).toBeInTheDocument();
    expect(screen.getByText(/다음 액션: 샘플 재발송 \(기한 2026-09-28\)/)).toBeInTheDocument();
    expect(screen.getByText("완료 2026-09-27")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "첨부 2건 보기" })).toBeInTheDocument();
  });

  it("기록이 없으면 안내를 보인다", async () => {
    stubApi(TRADER, BASE_ROW, [], []);
    await openDetail();

    expect(await screen.findByText("통신 기록이 없습니다.")).toBeInTheDocument();
  });

  it("무역 역할은 읽기만 — 등록 폼·완료·삭제 버튼이 없다", async () => {
    stubApi(TRADER, AGENCY_ROW);
    await openDetail();

    await screen.findByText("보완 서류 목록 수신");
    expect(screen.queryByRole("button", { name: "완료 처리" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "삭제" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "통신 기록 등록" })).not.toBeInTheDocument();
  });

  it("등록 — 오간 날 기본값은 KST 오늘, 상대 거래처는 대행사가 미리 선택된다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, AGENCY_ROW, calls, []);
    await openDetail();

    const form = await screen.findByRole("form", { name: "통신 기록 등록" });
    expect(within(form).getByLabelText("오간 날")).toHaveValue(TODAY);
    expect(within(form).getByLabelText("오간 날")).toHaveAttribute("max", TODAY);
    await waitFor(() => expect(within(form).getByLabelText("상대 거래처 (선택)")).toHaveValue("1"));
    // 다음 액션이 없으면 기한 입력이 잠긴다 — 주인 없는 날짜 금지(서버·DB와 같은 규칙).
    expect(within(form).getByLabelText("기한")).toBeDisabled();
    fireEvent.change(within(form).getByLabelText("요지"), { target: { value: "보완 요청 확인" } });
    fireEvent.change(within(form).getByLabelText("다음 액션 (선택)"), { target: { value: "서류 회신" } });
    expect(within(form).getByLabelText("기한")).not.toBeDisabled();
    fireEvent.change(within(form).getByLabelText("기한"), { target: { value: "2026-10-05" } });
    fireEvent.click(within(form).getByRole("button", { name: "기록" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.includes("comm-logs"))?.body).toEqual({
      subject_type: "CERTIFICATION",
      subject_id: 1,
      partner_id: 1,
      occurred_on: TODAY,
      summary: "보완 요청 확인",
      next_action: "서류 회신",
      next_action_due: "2026-10-05",
    });
  });

  it("다음 액션 없이 기한만 남아 있어도 서버로는 기한을 보내지 않는다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, BASE_ROW, calls, []);
    await openDetail();

    const form = await screen.findByRole("form", { name: "통신 기록 등록" });
    fireEvent.change(within(form).getByLabelText("다음 액션 (선택)"), { target: { value: "임시" } });
    fireEvent.change(within(form).getByLabelText("기한"), { target: { value: "2026-10-05" } });
    fireEvent.change(within(form).getByLabelText("다음 액션 (선택)"), { target: { value: "" } });
    fireEvent.change(within(form).getByLabelText("요지"), { target: { value: "메모" } });
    fireEvent.click(within(form).getByRole("button", { name: "기록" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const body = calls.find((c) => c.method === "POST" && c.url.includes("comm-logs"))?.body;
    expect(body).not.toHaveProperty("next_action");
    expect(body).not.toHaveProperty("next_action_due");
  });

  it("완료 처리는 오늘 날짜와 version을 보내고, 재개는 null을 보낸다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, AGENCY_ROW, calls);
    await openDetail();

    fireEvent.click(await screen.findByRole("button", { name: "완료 처리" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.includes("comm-logs/11"))).toBe(true));
    expect(calls.find((c) => c.method === "PATCH" && c.url.includes("comm-logs/11"))?.body).toEqual({
      version: 2,
      next_action_done_on: TODAY,
    });

    fireEvent.click(screen.getByRole("button", { name: "재개" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.url.includes("comm-logs/12"))).toBe(true));
    expect(calls.find((c) => c.method === "PATCH" && c.url.includes("comm-logs/12"))?.body).toEqual({
      version: 5,
      next_action_done_on: null,
    });
  });

  it("삭제는 확인을 거친다 — 취소하면 요청이 나가지 않는다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, AGENCY_ROW, calls);
    const confirm = vi.spyOn(window, "confirm");
    await openDetail();

    const buttons = await screen.findAllByRole("button", { name: "삭제" });
    confirm.mockReturnValueOnce(false);
    fireEvent.click(buttons[0] as HTMLElement);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    confirm.mockReturnValueOnce(true);
    fireEvent.click(buttons[0] as HTMLElement);
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.includes("comm-logs/11"))).toBe(true));
  });

  it("첨부 보기 — 소유 유형 COMM_LOG 문서를 조회해 보이고, 링크를 붙이면 소유자와 함께 요청한다", async () => {
    const calls: Recorded[] = [];
    stubApi(CERT_USER, AGENCY_ROW, calls);
    await openDetail();

    fireEvent.click(await screen.findByRole("button", { name: "첨부 2건 보기" }));
    const link = await screen.findByRole("link", { name: "링크 열기" });
    expect(link).toHaveAttribute("href", "https://example.com/reply.pdf");
    expect(calls.some((c) => c.url.includes("/v1/documents?owner_type=COMM_LOG&owner_id=11"))).toBe(true);

    const form = screen.getByRole("form", { name: "통신 기록 #11 첨부" });
    await waitFor(() => expect(within(form).getByRole("option", { name: "인증서" })).toBeInTheDocument());
    fireEvent.change(within(form).getByLabelText("서류 종류"), { target: { value: "CERTIFICATE" } });
    fireEvent.change(within(form).getByLabelText("주소(https://…)"), {
      target: { value: "https://example.com/new.pdf" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "첨부" }));

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.includes("/documents/links"))).toBe(true));
    expect(calls.find((c) => c.method === "POST" && c.url.includes("/documents/links"))?.body).toEqual({
      owner_type: "COMM_LOG",
      owner_id: 11,
      document_type: "CERTIFICATE",
      url: "https://example.com/new.pdf",
    });
  });

  it("서버가 거절하면 필드 문구를 그대로 보인다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(CERT_USER));
        if (input.includes("/v1/comm-logs") && init?.method === "POST") {
          return Promise.resolve(
            jsonResponse(
              {
                error: {
                  code: "COMMON.VALIDATION.INVALID_FIELD",
                  message: "입력값을 확인해 주세요.",
                  detail: { occurred_on: "실제로 오간 날은 오늘 이후일 수 없습니다." },
                  request_id: null,
                },
              },
              422,
            ),
          );
        }
        if (/\/v1\/certifications\/1$/.test(input)) return Promise.resolve(jsonResponse(BASE_ROW));
        if (input.includes("/v1/partners")) return Promise.resolve(jsonResponse(page([AGENCY])));
        return Promise.resolve(jsonResponse(page([])));
      }),
    );
    await openDetail();

    const form = await screen.findByRole("form", { name: "통신 기록 등록" });
    fireEvent.change(within(form).getByLabelText("요지"), { target: { value: "x" } });
    fireEvent.click(within(form).getByRole("button", { name: "기록" }));

    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((el) => el.textContent?.includes("실제로 오간 날은 오늘 이후일 수 없습니다."))).toBe(true);
  });
});
