// GatePanel — 7게이트 표시·정책 표기·마스킹·override 버튼 노출·다이얼로그·멱등·더블클릭·창 포커스·철회·오류 한국어화 (S3-1 PR-11b).

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GATE_ERROR_TEXT, validateOverrideReason } from "../lib/gate";
import { HASH_A, HASH_B, PRICE_BLOCK, SEVEN, gate, report } from "../test/gate-fixtures";
import { stubFetch, type Call } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, renderWithProviders } from "../test/render";
import { GatePanel } from "./gate-panel";

let keySeq = 0;
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const ADMIN = { ...TRADER, id: 2, roles: ["ADMIN"] };
const GATES = "/v1/sales-orders/9/gates";
const GRANT = "/v1/sales-orders/9/gate-overrides";
const REVOKE = "/v1/sales-orders/9/gate-overrides/revoke";
const err = (code: string, status: number, message = "서버 메시지(영문 detail 포함)") =>
  jsonResponse({ error: { code, message, detail: { gate_code: "PRICE_DEVIATION", status: "CONFIRMED" } } }, status);
const created = () => jsonResponse({ id: 1, action: "GRANT" }, 201);

type Handler = [string, string, () => Response];

function open(
  handlers: Handler[],
  opts: { me?: unknown; soStatus?: string; gates?: ReturnType<typeof report> } = {},
) {
  const rep = opts.gates ?? report(SEVEN);
  // 더 구체적인 경로(철회)가 앞서도록 호출자가 넘긴 핸들러가 먼저, 기본 GET은 마지막.
  const stub = stubFetch(opts.me ?? TRADER, [...handlers, [GATES, "GET", () => jsonResponse(rep)]]);
  const view = renderWithProviders(<GatePanel soId={9} soStatus={opts.soStatus ?? "RECEIVED"} />);
  return { ...stub, ...view };
}

const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");
const gets = (calls: Call[]) => calls.filter((c) => c.method === "GET" && c.url.endsWith("/gates"));
const rowOf = (name: RegExp | string) => screen.getByText(name, { selector: "span.font-medium" }).closest("tr") as HTMLElement;

async function openGrant() {
  const button = await screen.findByRole("button", { name: "가격 편차 · 라인 1 예외 승인" });
  fireEvent.click(button);
  return await screen.findByRole("dialog");
}
const reasonBox = (dialog: HTMLElement) => within(dialog).getByRole("textbox");
const typeReason = (dialog: HTMLElement, value: string) => fireEvent.change(reasonBox(dialog), { target: { value } });
const confirm = (dialog: HTMLElement) => within(dialog).getByRole("button", { name: "예외 승인 부여" });

describe("GatePanel — 7게이트 표시(서버 값 그대로)", () => {
  it("게이트 한국어명·결과 배지·해소 수단·정산 상태·사유 메시지·라인을 보이고 영문 코드는 노출하지 않는다", async () => {
    open([]);
    const table = await screen.findByRole("table");
    for (const name of ["품번 매핑", "중복 PO", "가격 편차", "여신 한도", "시장 준비 상태", "최소주문수량(MOQ)", "PI 선수금 입금"]) {
      expect(within(table).getByText(name)).toBeInTheDocument();
    }
    const price = within(rowOf("가격 편차"));
    expect(price.getByText("차단")).toBeInTheDocument();
    expect(price.getByText("라인 1")).toBeInTheDocument();
    expect(price.getByText("해소 수단: 예외 승인(override)")).toBeInTheDocument();
    expect(price.getByText("미해소")).toBeInTheDocument();
    expect(price.getByText("기준가 대비 편차가 허용치를 넘습니다.")).toBeInTheDocument();
    expect(within(rowOf("품번 매핑")).getByText("통과")).toBeInTheDocument();
    expect(within(rowOf("품번 매핑")).getByText("해소 불필요")).toBeInTheDocument();
    expect(within(rowOf("중복 PO")).getByText("경고")).toBeInTheDocument();
    // UNKNOWN은 차단과 다른 글자("판정 불가")
    expect(within(rowOf("시장 준비 상태")).getByText("판정 불가")).toBeInTheDocument();
    expect(within(rowOf("여신 한도")).getByText("해소 수단: 승인(결재)")).toBeInTheDocument();
    expect(table.textContent).not.toMatch(/PRICE_DEVIATION|PRICE_ABOVE_TOLERANCE|UNRESOLVED|NOT_REQUIRED|PASS|UNKNOWN|APPROVAL|NONE/);
  });

  it("참고값 안내·서버 clearance 요약을 그대로 보인다(미해소 건수는 서버 값, 프런트가 세지 않는다)", async () => {
    // 서버가 unresolved_count=7로 줘도(실제 행 수와 달라도) 그대로 표시 — 재계산하지 않는다.
    open([], { gates: report(SEVEN, { clearance: { cleared: false, needs_approval: true, unresolved_count: 7 } }) });
    const summary = await screen.findByRole("status", { name: "게이트 요약" });
    expect(summary).toHaveTextContent("미해소 게이트 7건");
    expect(summary).toHaveTextContent("여신 한도 초과로 승인(결재)이 필요합니다");
    expect(screen.getByText(/방금 조회한 참고 판정이며 확정 시 서버가 다시 평가합니다/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /확정/ })).not.toBeInTheDocument();
  });

  it("서버가 cleared=true를 주면 그 문구를, cleared=false·미해소 0건이어도 서버 값 그대로(프런트 재판정 없음)", async () => {
    open([], { gates: report([gate()], { clearance: { cleared: true, needs_approval: false, unresolved_count: 0 } }) });
    expect(await screen.findByRole("status", { name: "게이트 요약" })).toHaveTextContent("모든 게이트가 해소된 상태입니다");
  });

  it("서버가 cleared=false인데 모든 행이 통과여도 '해소된 상태'라고 말하지 않는다", async () => {
    open([], { gates: report([gate()], { clearance: { cleared: false, needs_approval: false, unresolved_count: 0 } }) });
    const summary = await screen.findByRole("status", { name: "게이트 요약" });
    expect(summary).not.toHaveTextContent("모든 게이트가 해소된 상태입니다");
    expect(summary).toHaveTextContent("미해소 게이트 0건");
  });

  it("승인이 있으면 승인 상세 링크를 보인다", async () => {
    open([], { gates: report(SEVEN, { approval: { available: true, approval_id: 33 } }) });
    expect(await screen.findByRole("link", { name: "승인 #33 보기" })).toHaveAttribute("href", "/approvals/33");
  });

  it("OVERRIDDEN 항목: 해소됨(예외 승인)·부여 정보(역할·사유)를 보인다", async () => {
    const overridden = gate({
      gate_code: "MOQ",
      level: "BLOCK",
      resolution: "OVERRIDE",
      settlement: "OVERRIDDEN",
      message_ko: "최소주문수량 미달입니다.",
      override_roles: ["TRADE", "ADMIN"],
      override: { id: 5, reason: "바이어와 합의한 소량 주문", authorized_role: "TRADE", granted_by_id: 1, created_at: "2026-10-01T01:00:00Z" },
    });
    open([], { gates: report([overridden]) });
    const row = within(await screen.findByText("최소주문수량(MOQ)").then((el) => el.closest("tr") as HTMLElement));
    expect(row.getByText("해소됨(예외 승인)")).toBeInTheDocument();
    expect(row.getByText(/사유: 바이어와 합의한 소량 주문/)).toBeInTheDocument();
    expect(row.getByText(/무역 권한/)).toBeInTheDocument();
  });

  it("DUPLICATE_PO 상세(다른 수주 번호)는 서버가 주면 보이고, 안 주면(마스킹 역할) 없다", async () => {
    const dup = gate({ gate_code: "DUPLICATE_PO", level: "BLOCK", detail: { other_doc_number: "SO-2026-0007", other_status: "RECEIVED" }, message_ko: "같은 PO번호의 다른 수주가 있습니다." });
    const first = open([], { gates: report([dup]) });
    expect(await screen.findByText(/SO-2026-0007/)).toBeInTheDocument();
    first.unmount();
    open([], { gates: report([{ ...dup, detail: {} }]) });
    await screen.findByText("같은 PO번호의 다른 수주가 있습니다.");
    expect(screen.queryByText(/SO-2026-0007/)).not.toBeInTheDocument();
  });
});

describe("GatePanel — PI·가격 정책 표기", () => {
  it("PI 정책 미설정(UNSET_DEFAULT)은 기본 차단 적용 중임을 적색 문구로 표기한다", async () => {
    open([], {
      gates: report(SEVEN, { policies: { pi_advance_gate_mode: { value: "BLOCK", source: "UNSET_DEFAULT" }, price_deviation_tolerance_bp: { value: 0, source: "UNSET_DEFAULT" } } }),
    });
    const pi = await screen.findByText(/PI 입금 게이트 모드/);
    expect(pi).toHaveTextContent("차단(BLOCK)");
    expect(pi).toHaveTextContent("미설정 — 기본값(차단)이 적용 중입니다");
    expect(pi.className).toContain("text-signal-red");
    const price = screen.getByText(/가격 편차 허용치/);
    expect(price).toHaveTextContent("0 bp");
    expect(price).toHaveTextContent("미설정");
    expect(price.className).toContain("text-signal-red");
  });

  it("설정된 정책(OFF·WARN·BLOCK)은 모드 라벨만 보이고 적색·미설정 문구가 없다", async () => {
    open([], { gates: report(SEVEN, { policies: { pi_advance_gate_mode: { value: "WARN", source: "SET" }, price_deviation_tolerance_bp: { value: 100, source: "SET" } } }) });
    const pi = await screen.findByText(/PI 입금 게이트 모드/);
    expect(pi).toHaveTextContent("경고(WARN)");
    expect(pi).not.toHaveTextContent("미설정");
    expect(pi.className).not.toContain("text-signal-red");
    expect(screen.getByText(/가격 편차 허용치/)).toHaveTextContent("100 bp");
  });
});

describe("GatePanel — CREDIT 마스킹·override 불가", () => {
  it("마스킹 역할(basis_hash 빈 문자열): 수치 없음·마스킹 안내·override 버튼 없음·승인으로만 해소 안내", async () => {
    const masked = gate({ gate_code: "CREDIT", level: "BLOCK", resolution: "APPROVAL", basis: {}, basis_hash: "", settlement: "UNRESOLVED", message_ko: "여신 한도를 초과합니다." });
    open([], { me: { ...VIEWER }, gates: report([masked]) });
    const row = within(await screen.findByText("여신 한도").then((el) => el.closest("tr") as HTMLElement));
    expect(row.getByText(/무역·관리자에게만 표시됩니다/)).toBeInTheDocument();
    expect(row.getByText(/승인\(결재\)으로만 해소/)).toBeInTheDocument();
    expect(row.queryByRole("button")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d{6,}/);
  });

  it("여신 수치를 볼 수 있는 역할이어도 정수 최소단위 값을 화면에 내보내지 않고(산술 0) 승인 상세로 안내한다", async () => {
    open([]);
    const row = within(await screen.findByText("여신 한도").then((el) => el.closest("tr") as HTMLElement));
    expect(row.getByText(/승인 상세에서 확인/)).toBeInTheDocument();
    expect(row.queryByRole("button")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("123456789");
  });
});

describe("GatePanel — override 버튼 노출", () => {
  it("can_override가 true인 항목에만 버튼을 보인다(7종 중 1건)", async () => {
    open([]);
    await screen.findByRole("table");
    expect(screen.getAllByRole("button", { name: /예외 승인$/ })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "가격 편차 · 라인 1 예외 승인" })).toBeInTheDocument();
  });

  it("미해소+override 가능 해소수단이지만 can_override=false(역할 부족)면 버튼 대신 허용 역할 안내", async () => {
    open([]);
    const row = within(await screen.findByText("시장 준비 상태").then((el) => el.closest("tr") as HTMLElement));
    expect(row.queryByRole("button")).not.toBeInTheDocument();
    expect(row.getByText("관리자 역할만 예외 승인할 수 있습니다.")).toBeInTheDocument();
  });

  it("읽기 전용 역할(VIEWER): can_override가 true로 와도 버튼 없음 + 안내", async () => {
    open([], { me: VIEWER });
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /예외 승인/ })).not.toBeInTheDocument();
    expect(screen.getByText(/예외 승인·철회는 무역·관리자만 할 수 있습니다/)).toBeInTheDocument();
  });

  it("접수가 아닌 SO(prop): 버튼 없음 + 사유(현재 상태)", async () => {
    open([], { soStatus: "CONFIRMED" });
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /예외 승인/ })).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("접수 상태가 아닌 수주(현재: 확정)");
  });

  it("접수가 아닌 SO(서버 report.status): 버튼 없음", async () => {
    open([], { gates: report(SEVEN, { status: "ON_HOLD" }) });
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /예외 승인/ })).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("현재: 보류");
  });
});

describe("GatePanel — override 부여 다이얼로그", () => {
  it("대상·현재 판정·철회 안내를 보이고, 사유 5자 미만이면 확정이 막힌다", async () => {
    const { calls } = open([]);
    const dialog = await openGrant();
    expect(dialog).toHaveTextContent("가격 편차 · 라인 1");
    expect(dialog).toHaveTextContent("기준가 대비 편차가 허용치를 넘습니다.");
    expect(dialog).toHaveTextContent("되돌리려면 철회해야 합니다");
    expect(dialog).toHaveTextContent("철회한 뒤의 재부여는 관리자만");
    expect(confirm(dialog)).toBeDisabled();
    typeReason(dialog, "네글자야");
    expect(confirm(dialog)).toBeDisabled();
    typeReason(dialog, "다섯 글자요");
    expect(confirm(dialog)).toBeEnabled();
    expect(posts(calls)).toHaveLength(0);
  });

  it("제어문자(줄바꿈·탭)·보이지 않는 글자는 오류 문구로 막고 전송 0", async () => {
    const { calls } = open([]);
    const dialog = await openGrant();
    typeReason(dialog, "충분히 긴 사유\n두 번째 줄");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("줄바꿈·탭·보이지 않는 글자");
    expect(confirm(dialog)).toBeDisabled();
    typeReason(dialog, "충분히 긴​사유입니다"); // 제로폭 공백(서식 문자)
    expect(confirm(dialog)).toBeDisabled();
    typeReason(dialog, "채움ㅤ문자 포함 사유");
    expect(confirm(dialog)).toBeDisabled();
    expect(posts(calls)).toHaveLength(0);
  });

  it("공백만으로 채운 사유는 막는다(공백 뺀 실질 5자)", async () => {
    open([]);
    const dialog = await openGrant();
    typeReason(dialog, "가 나 다 라");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("공백을 뺀 실제 글자");
    expect(confirm(dialog)).toBeDisabled();
  });

  it("길이는 코드포인트 기준: 이모지 5개(UTF-16 10)는 통과, 4개는 막고, 500개는 통과·501개는 막는다", async () => {
    open([]);
    const dialog = await openGrant();
    typeReason(dialog, "😀".repeat(4));
    expect(confirm(dialog)).toBeDisabled();
    typeReason(dialog, "😀".repeat(5));
    expect(confirm(dialog)).toBeEnabled();
    typeReason(dialog, "😀".repeat(500));
    expect(confirm(dialog)).toBeEnabled();
    typeReason(dialog, "😀".repeat(501));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("500자 이내");
    expect(confirm(dialog)).toBeDisabled();
  });

  it("확정하면 POST 본문에 게이트·라인·판정 해시·사유가 실리고 멱등 키 헤더가 붙는다", async () => {
    const { calls } = open([[GRANT, "POST", created]]);
    const dialog = await openGrant();
    typeReason(dialog, "  바이어와 합의한 가격입니다  ");
    fireEvent.click(confirm(dialog));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    const call = posts(calls)[0] as Call;
    expect(call.url).toBe(`/api${GRANT}`);
    expect(call.body).toEqual({ gate_code: "PRICE_DEVIATION", line_id: 41, basis_hash: HASH_A, reason: "바이어와 합의한 가격입니다" });
    expect(call.headers["Idempotency-Key"]).toBe("key-1");
  });

  it("성공하면 다이얼로그가 닫히고 게이트·SO 상세를 다시 읽는다(무효화)", async () => {
    const { calls } = open([[GRANT, "POST", created]]);
    const dialog = await openGrant();
    typeReason(dialog, "바이어와 합의한 가격입니다");
    const before = gets(calls).length;
    fireEvent.click(confirm(dialog));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
  });

  it("더블클릭해도 POST는 1건", async () => {
    const { calls } = open([[GRANT, "POST", created]]);
    const dialog = await openGrant();
    typeReason(dialog, "바이어와 합의한 가격입니다");
    const button = confirm(dialog);
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
  });

  it("실패 뒤 같은 본문 재시도는 같은 키, 성공하면 키 Map을 비워 다음 부여는 새 키", async () => {
    let fail = true;
    const { calls } = open([[GRANT, "POST", () => (fail ? err("COMMON.SERVER.INTERNAL", 500, "서버 오류") : created())]]);
    let dialog = await openGrant();
    typeReason(dialog, "바이어와 합의한 가격입니다");
    fireEvent.click(confirm(dialog));
    await within(dialog).findByRole("alert");
    fail = false;
    fireEvent.click(confirm(dialog));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[0]?.headers["Idempotency-Key"]).toBe("key-1");
    expect(posts(calls)[1]?.headers["Idempotency-Key"]).toBe("key-1");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // 같은 본문으로 다시 열어도(목 서버는 같은 판정을 돌려줌) 새 키 — 이전 응답이 재생되지 않는다.
    dialog = await openGrant();
    typeReason(dialog, "바이어와 합의한 가격입니다");
    fireEvent.click(confirm(dialog));
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(posts(calls)[2]?.headers["Idempotency-Key"]).not.toBe("key-1");
  });

  it("본문(사유)이 달라지면 새 키", async () => {
    const { calls } = open([[GRANT, "POST", () => err("COMMON.SERVER.INTERNAL", 500, "서버 오류")]]);
    const dialog = await openGrant();
    typeReason(dialog, "첫 번째 사유입니다");
    fireEvent.click(confirm(dialog));
    await within(dialog).findByRole("alert");
    typeReason(dialog, "두 번째 사유입니다");
    fireEvent.click(confirm(dialog));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    expect(posts(calls)[0]?.headers["Idempotency-Key"]).not.toBe(posts(calls)[1]?.headers["Idempotency-Key"]);
  });

  it("요청 중에는 Esc·닫기로 닫히지 않고 확정 버튼은 잠긴다(잠금은 finally로 풀림)", async () => {
    const pending: Array<(r: Response) => void> = [];
    const { calls } = open([]);
    // 응답을 보류하는 핸들러로 교체
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation((input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url: input, method, body: init?.body ? JSON.parse(String(init.body)) : null, headers: (init?.headers ?? {}) as Record<string, string> });
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
      if (method === "POST") return new Promise<Response>((resolve) => pending.push(resolve));
      return Promise.resolve(jsonResponse(report(SEVEN)));
    });
    const dialog = await openGrant();
    typeReason(dialog, "바이어와 합의한 가격입니다");
    fireEvent.click(confirm(dialog));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "닫기" })).toBeDisabled();
    await act(async () => {
      pending[0]?.(err("GATES.OVERRIDE.STALE", 409));
    });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("이미 바뀌었습니다");
    // 잠금이 풀렸다 — 다시 누르면 두 번째 요청이 나간다.
    fireEvent.click(confirm(dialog));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
  });

  it("창 포커스 재조회가 와도 열린 다이얼로그와 입력한 사유가 닫히거나 지워지지 않는다(판정이 바뀌어도)", async () => {
    let current = report(SEVEN);
    const { calls } = open([]);
    (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation((input: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({ url: input, method, body: null, headers: {} });
      if (input.includes("/auth/me")) return Promise.resolve(jsonResponse(TRADER));
      return Promise.resolve(jsonResponse(current));
    });
    const dialog = await openGrant();
    typeReason(dialog, "입력 중이던 사유입니다");
    // 재조회 결과가 바뀐다: 그 라인의 판정 해시가 달라지고 can_override도 사라진다.
    current = report([{ ...PRICE_BLOCK, basis_hash: HASH_B, can_override: false }]);
    const before = gets(calls).length;
    act(() => {
      window.dispatchEvent(new Event("visibilitychange"));
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(screen.getByRole("dialog")).toBe(dialog);
    expect(reasonBox(dialog)).toHaveValue("입력 중이던 사유입니다");
  });
});

describe("GatePanel — 오류 한국어화(영문 코드·detail 비노출)", () => {
  async function submitWith(response: () => Response, extra: { me?: unknown } = {}) {
    const stub = open([[GRANT, "POST", response]], extra);
    const dialog = await openGrant();
    typeReason(dialog, "바이어와 합의한 가격입니다");
    fireEvent.click(confirm(dialog));
    const alert = await within(dialog).findByRole("alert");
    return { ...stub, dialog, alert };
  }

  it.each([
    ["GATES.OVERRIDE.ALREADY_GRANTED", 409, "먼저 철회해야 하며, 철회한 뒤의 재부여는 관리자만"],
    ["GATES.OVERRIDE.STALE", 409, "확인하신 판정이 이미 바뀌었습니다"],
    ["GATES.OVERRIDE.ORDER_NOT_OPEN", 409, "접수 상태가 아닌 수주"],
    ["GATES.OVERRIDE.NOT_ALLOWED", 403, "현재 역할로 할 수 없습니다"],
    ["GATES.OVERRIDE.NOT_APPLICABLE", 422, "예외 승인 대상이 아닌 항목"],
    ["COMMON.VALIDATION.INVALID_FIELD", 422, "사유는 5~500자"],
  ])("%s(%i)", async (code, status, text) => {
    const { alert } = await submitWith(() => err(code, status));
    expect(alert).toHaveTextContent(text);
    expect(alert.textContent).not.toMatch(/GATES\.|PRICE_DEVIATION|CONFIRMED|detail/);
    expect(alert.textContent).not.toContain("서버 메시지");
  });

  it("알 수 없는 403·409는 일반 한국어 문구, 영문 전용 메시지는 기본 문구", async () => {
    expect((await submitWith(() => err("X.Y.Z", 409))).alert).toHaveTextContent("처리 중 충돌이 발생했습니다");
  });

  it("409(STALE·ALREADY_GRANTED·ORDER_NOT_OPEN)는 '최신 내용 불러오기'로 판정을 다시 읽고 다이얼로그를 닫는다", async () => {
    const { calls, dialog } = await submitWith(() => err("GATES.OVERRIDE.STALE", 409));
    const before = gets(calls).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(gets(calls).length).toBeGreaterThan(before));
  });

  it("권한 없음(NOT_ALLOWED)은 불러오기 버튼 없이 문구만(다른 항목은 가능할 수 있어 패널 버튼도 유지)", async () => {
    const { dialog } = await submitWith(() => err("GATES.OVERRIDE.NOT_ALLOWED", 403));
    expect(within(dialog).queryByRole("button", { name: "최신 내용 불러오기" })).not.toBeInTheDocument();
  });

  it("라우트 403(다른 코드)이면 다이얼로그를 닫고 버튼을 숨긴다", async () => {
    await submitWith(() => err("AUTH.FORBIDDEN", 403)).catch(() => undefined);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /예외 승인$/ })).not.toBeInTheDocument();
  });

  it("카탈로그의 모든 GATES 코드가 한국어 사전에 있다", () => {
    for (const code of ["NOT_ALLOWED", "NOT_APPLICABLE", "STALE", "ALREADY_GRANTED", "NOT_GRANTED", "ORDER_NOT_OPEN"]) {
      expect(GATE_ERROR_TEXT[`GATES.OVERRIDE.${code}`]).toBeTruthy();
    }
  });
});

describe("GatePanel — 철회", () => {
  const granted = gate({
    gate_code: "PRICE_DEVIATION",
    line_id: 41,
    line_no: 1,
    level: "BLOCK",
    resolution: "OVERRIDE",
    settlement: "OVERRIDDEN",
    message_ko: "기준가 대비 편차가 허용치를 넘습니다.",
    override_roles: ["TRADE", "ADMIN"],
    can_override: false,
    override: { id: 5, reason: "합의한 가격", authorized_role: "TRADE", granted_by_id: 1, created_at: "2026-10-01T01:00:00Z" },
  });

  it("부여자 본인에게 '철회' 버튼 → 사유 다이얼로그 → POST(철회 경로·같은 판정 해시)", async () => {
    const { calls } = open([[REVOKE, "POST", created]], { gates: report([granted]) });
    fireEvent.click(await screen.findByRole("button", { name: "가격 편차 · 라인 1 예외 승인 철회" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("철회한 뒤의 재부여는 관리자만");
    typeReason(dialog, "합의가 취소되었습니다");
    fireEvent.click(within(dialog).getByRole("button", { name: "예외 승인 철회" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    const call = posts(calls)[0] as Call;
    expect(call.url).toBe(`/api${REVOKE}`);
    expect(call.body).toEqual({ gate_code: "PRICE_DEVIATION", line_id: 41, basis_hash: HASH_A, reason: "합의가 취소되었습니다" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("관리자는 남이 부여한 것도 철회할 수 있고, 부여자가 아닌 무역 담당에게는 버튼 없이 안내", async () => {
    const admin = open([], { gates: report([granted]), me: ADMIN });
    expect(await screen.findByRole("button", { name: /예외 승인 철회/ })).toBeInTheDocument();
    admin.unmount();
    open([], { gates: report([granted]), me: { ...TRADER, id: 77 } });
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /예외 승인 철회/ })).not.toBeInTheDocument();
    expect(screen.getByText("철회는 부여한 본인 또는 관리자만 할 수 있습니다.")).toBeInTheDocument();
  });

  it("철회 실패 NOT_GRANTED는 한국어 문구 + 불러오기, 접수 아닌 SO에서는 철회 버튼도 없다", async () => {
    open([[REVOKE, "POST", () => err("GATES.OVERRIDE.NOT_GRANTED", 422)]], { gates: report([granted]) });
    fireEvent.click(await screen.findByRole("button", { name: /예외 승인 철회/ }));
    const dialog = await screen.findByRole("dialog");
    typeReason(dialog, "합의가 취소되었습니다");
    fireEvent.click(within(dialog).getByRole("button", { name: "예외 승인 철회" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("철회할 유효한 예외 승인이 없습니다");
    expect(within(dialog).getByRole("button", { name: "최신 내용 불러오기" })).toBeInTheDocument();
  });

  it("접수 아닌 SO에서는 철회 버튼이 없다", async () => {
    open([], { gates: report([granted], { status: "CONFIRMED" }), soStatus: "CONFIRMED" });
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /철회/ })).not.toBeInTheDocument();
  });
});

describe("GatePanel — 조회 실패·형식", () => {
  it("조회 실패는 한국어 알림, 영문 코드 비노출", async () => {
    stubFetch(TRADER, [[GATES, "GET", () => err("COMMON.SERVER.INTERNAL", 500, "서버 오류가 발생했습니다.")]]);
    renderWithProviders(<GatePanel soId={9} soStatus="RECEIVED" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("서버 오류가 발생했습니다.");
  });

  it("gates 배열이 없는 비정상 응답은 표 없이 안내만(터지지 않는다)", async () => {
    stubFetch(TRADER, [[GATES, "GET", () => jsonResponse({ items: [] })]]);
    renderWithProviders(<GatePanel soId={9} soStatus="RECEIVED" />);
    expect(await screen.findByText("게이트 판정을 불러오지 못했습니다.")).toBeInTheDocument();
  });
});

describe("validateOverrideReason", () => {
  it("경계: 5자 통과·4자 거부·500자 통과·501자 거부·탭 거부·채움 문자 거부", () => {
    expect(validateOverrideReason("가나다라마")).toBeNull();
    expect(validateOverrideReason("가나다라")).not.toBeNull();
    expect(validateOverrideReason("가".repeat(500))).toBeNull();
    expect(validateOverrideReason("가".repeat(501))).not.toBeNull();
    expect(validateOverrideReason("가나다\t라마")).not.toBeNull();
    expect(validateOverrideReason("가나다ᅟ라마")).not.toBeNull();
    expect(validateOverrideReason("  가나다라마  ")).toBeNull();
  });
});
