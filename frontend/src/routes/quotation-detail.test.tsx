// 견적 상세 — 상태별 버튼·동결 읽기 전용·낙관 잠금 409·다이얼로그·타임라인 (S3-1 PR-5b).

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { LINE, LOG, detail, stubFetch } from "../test/qt-fixtures";
import { TRADER, VIEWER, jsonResponse, page, renderWithProviders } from "../test/render";

let keySeq = 0;
// 호출마다 다른 값 — "다이얼로그 열 때 1개·재시도는 같은 키"를 실제로 검증한다.
beforeEach(() => {
  keySeq = 0;
  vi.stubGlobal("crypto", { randomUUID: () => `key-${++keySeq}` });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const FROZEN_AT = "2026-09-30T02:00:00Z";
const REFS: Array<[string, string, () => Response]> = [
  ["/v1/quotations/7/status-log", "GET", () => jsonResponse(LOG)],
  ["/v1/markets", "GET", () => jsonResponse(page([{ id: 1, code: "US", name_ko: "미국" }]))],
  ["/v1/system/currencies", "GET", () => jsonResponse(page([{ code: "USD", minor_units: 2 }]))],
  ["/v1/users/lookup", "GET", () => jsonResponse(page([{ id: 1, display_name: "무역 담당" }]))],
];

/** 서버 상태를 흉내 내는 가변 저장소 — 쓰기 응답 뒤 재조회가 새 값을 돌려주게 한다. */
const server = { qt: detail() };

function open(
  qt: ReturnType<typeof detail>,
  extra: Array<[string, string, () => Response]> = [],
  me: unknown = TRADER,
) {
  server.qt = qt;
  const stub = stubFetch(me, [
    ...extra,
    ...REFS,
    // ★ 부분 일치라 status-log 같은 하위 경로보다 뒤에 둔다.
    ["/v1/quotations/7", "GET", () => jsonResponse(server.qt)],
  ]);
  renderWithProviders(<AppRoutes />, { route: "/quotations/7" });
  return stub;
}

const buttons = () => screen.getAllByRole("button").map((b) => b.textContent);

describe("견적 상세 — 상태별 동작 버튼", () => {
  it("초안: 발행·취소 가능, 개정 불가 / 헤더·라인 편집 폼이 보인다", async () => {
    open(detail());
    expect(await screen.findByRole("heading", { name: /QT-2026-0001/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "발행" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "개정본 작성" })).not.toBeInTheDocument();
    expect(screen.getByRole("form", { name: "견적 헤더 편집" })).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "라인 추가" })).toBeInTheDocument();
  });

  it("발행: 개정·취소 가능, 발행 불가 / 가격·조건은 읽기 전용, 내부 메모는 편집 가능", async () => {
    open(detail({ status: "ISSUED", frozen_at: FROZEN_AT }));
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    expect(screen.queryByRole("button", { name: "발행" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "개정본 작성" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
    // 동결: CONTENT 편집 폼·라인 추가·수정·제외가 없다.
    expect(screen.queryByRole("form", { name: "견적 헤더 편집" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "라인 추가" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "제외" })).not.toBeInTheDocument();
    expect(screen.getByText("선수금 T/T · 선수금 30% · 잔금 B/L일 기준 30일")).toBeInTheDocument();
    // FREE 열은 동결 후에도.
    expect(screen.getByRole("form", { name: "내부 메모·담당자" })).toBeInTheDocument();
  });

  it.each(["CANCELLED", "EXPIRED", "CONVERTED"])("%s: 전이 버튼이 하나도 없다", async (status) => {
    open(detail({ status, frozen_at: FROZEN_AT }));
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    for (const label of ["발행", "개정본 작성", "취소"]) {
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
    expect(buttons()).not.toContain("발행");
  });

  it("조회 전용 역할은 쓰기 버튼·폼이 없다", async () => {
    open(detail(), [], VIEWER);
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    expect(screen.queryByRole("button", { name: "발행" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "견적 헤더 편집" })).not.toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "내부 메모·담당자" })).not.toBeInTheDocument();
  });

  it("합계·단가·라인 금액은 서버 문자열 그대로 보인다", async () => {
    open(detail());
    await screen.findByRole("heading", { name: /QT-2026-0001/ });
    expect(screen.getByText("125.00 USD")).toBeInTheDocument();
    expect(screen.getByText("12.50")).toBeInTheDocument();
    expect(screen.getByText("수분 세럼")).toBeInTheDocument();
  });

  it("상태 이력 타임라인이 상세에 붙어 사유를 보인다", async () => {
    open(detail());
    expect(await screen.findByText("사유: 바이어 요청")).toBeInTheDocument();
    expect(screen.getByText("발행 → 취소")).toBeInTheDocument();
  });

  it("없는 견적은 404 문구와 목록 링크", async () => {
    stubFetch(TRADER, [
      ["/v1/quotations/7", "GET", () => jsonResponse({ error: { code: "NOT_FOUND", message: "x" } }, 404)],
    ]);
    renderWithProviders(<AppRoutes />, { route: "/quotations/7" });
    expect(await screen.findByRole("alert")).toHaveTextContent("견적을 찾을 수 없습니다.");
    expect(screen.getByRole("link", { name: "견적 목록으로" })).toBeInTheDocument();
  });
});

describe("견적 상세 — 발행·취소·개정 다이얼로그", () => {
  it("발행은 위험 안내 다이얼로그를 거쳐 version·멱등 키와 함께 POST한다", async () => {
    const issued = detail({ status: "ISSUED", frozen_at: FROZEN_AT, version: 5 });
    const { calls } = open(detail(), [["/v1/quotations/7/issue", "POST", () => jsonResponse((server.qt = issued))]]);
    fireEvent.click(await screen.findByRole("button", { name: "발행" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/동결/)).toBeInTheDocument();
    expect(within(dialog).getByText(/다시 고칠 수 없습니다/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "발행" }));
    await waitFor(() => {
      const post = calls.find((c) => c.url.endsWith("/v1/quotations/7/issue"));
      expect(post?.body).toEqual({ version: 4 });
      expect(post?.headers["Idempotency-Key"]).toBe("key-1");
    });
    // 발행 뒤: 다이얼로그가 닫히고 읽기 전용이 된다.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "개정본 작성" })).toBeInTheDocument();
  });

  it("발행 검증 실패(422)는 서버 문구와 누락 필드를 다이얼로그에 보인다", async () => {
    open(detail(), [
      [
        "/v1/quotations/7/issue",
        "POST",
        () =>
          jsonResponse(
            { error: { code: "TRADE_DOCS.DOCUMENT.INCOMPLETE", message: "발행에 필요한 값이 비어 있습니다.", detail: { valid_until: "유효기간이 필요합니다." } } },
            422,
          ),
      ],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "발행" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "발행" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "발행에 필요한 값이 비어 있습니다. (유효기간이 필요합니다.)",
    );
  });

  it("취소는 사유가 비어 있으면 확정할 수 없고, 사유와 함께 transitions로 간다", async () => {
    const { calls } = open(detail({ status: "ISSUED", frozen_at: FROZEN_AT }), [
      [
        "/v1/quotations/7/transitions",
        "POST",
        () => jsonResponse((server.qt = detail({ status: "CANCELLED", frozen_at: FROZEN_AT, version: 5 }))),
      ],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/되돌릴 수 없습니다/)).toBeInTheDocument();
    const confirm = within(dialog).getByRole("button", { name: "취소 확정" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), {
      target: { value: "  바이어 요청  " },
    });
    fireEvent.click(confirm);
    await waitFor(() => {
      const post = calls.find((c) => c.url.endsWith("/v1/quotations/7/transitions"));
      expect(post?.body).toEqual({ to: "CANCELLED", version: 4, reason: "바이어 요청" });
    });
    await waitFor(() => expect(screen.queryByRole("button", { name: "개정본 작성" })).not.toBeInTheDocument());
  });

  it("개정본 작성은 새 초안 화면으로 이동한다", async () => {
    const { calls } = open(detail({ status: "ISSUED", frozen_at: FROZEN_AT }), [
      ["/v1/quotations/7/revisions", "POST", () => jsonResponse(detail({ id: 8, doc_number: "QT-2026-0002", copied_from_id: 7 }), 201)],
      ["/v1/quotations/8/status-log", "GET", () => jsonResponse(page([]))],
      ["/v1/quotations/8", "GET", () => jsonResponse(detail({ id: 8, doc_number: "QT-2026-0002", copied_from_id: 7 }))],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "개정본 작성" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/원본이 자동으로 취소/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "개정본 작성" }));
    expect(await screen.findByRole("heading", { name: /QT-2026-0002/ })).toBeInTheDocument();
    expect(calls.find((c) => c.url.endsWith("/revisions"))?.body).toEqual({ version: 4 });
  });

  it("다이얼로그는 Esc로 닫힌다", async () => {
    open(detail());
    fireEvent.click(await screen.findByRole("button", { name: "취소" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});

describe("견적 상세 — 낙관 잠금·편집", () => {
  const CONFLICT = () =>
    jsonResponse(
      { error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "다른 사용자가 먼저 수정했습니다." } },
      409,
    );

  it("헤더 저장은 화면이 본 version을 싣고, 409면 안내와 '최신 내용 불러오기'를 보인다", async () => {
    const { calls } = open(detail(), [["/v1/quotations/7", "PATCH", CONFLICT]]);
    await screen.findByRole("form", { name: "견적 헤더 편집" });
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent("다른 곳에서 이 견적이 먼저 수정되었습니다");
    const patch = calls.find((c) => c.method === "PATCH" && c.url.endsWith("/v1/quotations/7"));
    expect(patch?.body).toMatchObject({ version: 4, dest_market_code: "US", doc_date: "2026-09-30" });
    // 서버 소유 필드는 보내지 않는다.
    expect(patch?.body).not.toHaveProperty("status");
    expect(patch?.body).not.toHaveProperty("total_amount");
    const before = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length;
    fireEvent.click(within(banner).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => {
      const after = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length;
      expect(after).toBeGreaterThan(before);
    });
  });

  it("라인 추가는 SKU를 고른 뒤에만 열리고, 헤더 version과 함께 POST하며 응답 version·합계를 반영한다", async () => {
    const added = { line: { ...LINE, id: 12, line_no: 2, sku_id: 6, sku_code: "SKU-002" }, header_version: 5, total_amount: 25000, total_text: "250.00" };
    const { calls } = open(detail(), [
      [
        "/v1/quotations/7/lines",
        "POST",
        () => {
          server.qt = detail({ version: 5, total_text: "250.00", lines: [LINE, added.line] });
          return jsonResponse(added, 201);
        },
      ],
      ["/v1/skus", "GET", () => jsonResponse(page([{ id: 6, sku_code: "SKU-002", name_ko: "선크림" }]))],
    ]);
    const form = await screen.findByRole("form", { name: "라인 추가" });
    const submit = within(form).getByRole("button", { name: "라인 추가" });
    expect(submit).toBeDisabled();
    fireEvent.focus(within(form).getByRole("combobox", { name: "SKU" }));
    fireEvent.click(await screen.findByRole("option", { name: "SKU-002 선크림" }));
    fireEvent.change(within(form).getByLabelText("수량"), { target: { value: "10" } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/v1/quotations/7/lines"));
      // 단가를 비웠으므로 unit_price 키가 없다(서버가 마스터 판가) — 화면이 단가를 만들지 않는다.
      expect(post?.body).toEqual({ version: 4, sku_id: 6, quantity: 10, is_free: false });
    });
    expect(await screen.findByText("250.00 USD")).toBeInTheDocument();
    expect(await screen.findByText("SKU-002")).toBeInTheDocument();
  });

  it("라인 수정은 바뀐 필드만 version과 함께 PATCH하고, 응답의 새 version을 반영한다", async () => {
    const updated = { line: { ...LINE, quantity: 20 }, header_version: 5, total_amount: 25000, total_text: "250.00" };
    const { calls } = open(detail(), [
      [
        "/lines/11",
        "PATCH",
        () => {
          server.qt = detail({ version: 5, total_text: "250.00", lines: [updated.line] });
          return jsonResponse(updated);
        },
      ],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "수정" }));
    const form = screen.getByRole("form", { name: "라인 1 수정" });
    fireEvent.change(within(form).getByLabelText("수량"), { target: { value: "20" } });
    fireEvent.click(within(form).getByRole("button", { name: "저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH" && c.url.endsWith("/lines/11"));
      expect(patch?.body).toEqual({ version: 4, quantity: 20 });
    });
    expect(await screen.findByText("250.00 USD")).toBeInTheDocument();
  });

  it("라인 제외는 확인 후 DELETE ?version= 으로 간다", async () => {
    const gone = { line: null, header_version: 5, total_amount: 0, total_text: "0.00" };
    const { calls } = open(detail(), [["/lines/11", "DELETE", () => jsonResponse(gone)]]);
    fireEvent.click(await screen.findByRole("button", { name: "제외" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "제외" }));
    await waitFor(() => {
      expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/lines/11?version=4"))).toBe(true);
    });
  });

  it("라인이 있었던 견적은 통화 변경이 잠긴다", async () => {
    open(detail({ last_line_no: 1 }));
    const form = await screen.findByRole("form", { name: "견적 헤더 편집" });
    expect(within(form).getByLabelText(/^통화/)).toBeDisabled();
  });

  it("내부 메모·담당자는 /meta로 version과 함께 간다(동결 후에도)", async () => {
    const { calls } = open(detail({ status: "ISSUED", frozen_at: FROZEN_AT }), [
      ["/v1/quotations/7/meta", "PATCH", () => jsonResponse(detail({ status: "ISSUED", frozen_at: FROZEN_AT, version: 5, internal_note: "메모" }))],
    ]);
    const form = await screen.findByRole("form", { name: "내부 메모·담당자" });
    fireEvent.change(within(form).getByLabelText("내부 메모"), { target: { value: "메모" } });
    fireEvent.click(within(form).getByRole("button", { name: "메모·담당자 저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.url.endsWith("/v1/quotations/7/meta"));
      expect(patch?.body).toEqual({ version: 4, internal_note: "메모", assignee_id: 1 });
    });
  });
});

const failIssue = () =>
  jsonResponse({ error: { code: "TRADE_DOCS.DOCUMENT.INCOMPLETE", message: "발행에 필요한 값이 비어 있습니다." } }, 422);
const CONFLICT_RESPONSE = () =>
  jsonResponse({ error: { code: "COMMON.CONCURRENCY.VERSION_CONFLICT", message: "충돌" } }, 409);

describe("견적 상세 — 멱등 키·더블클릭", () => {
  it("발행 재시도는 같은 멱등 키를 쓰고, 다이얼로그를 다시 열면 새 키를 쓴다", async () => {
    const { calls } = open(detail(), [["/v1/quotations/7/issue", "POST", failIssue]]);
    fireEvent.click(await screen.findByRole("button", { name: "발행" }));
    let dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "발행" }));
    await within(dialog).findByRole("alert");
    fireEvent.click(within(dialog).getByRole("button", { name: "발행" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/issue"))).toHaveLength(2));
    const keys = calls.filter((c) => c.url.endsWith("/issue")).map((c) => c.headers["Idempotency-Key"]);
    expect(keys[0]).toBe(keys[1]);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "발행" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "발행" }));
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/issue"))).toHaveLength(3));
    expect(calls.filter((c) => c.url.endsWith("/issue"))[2]?.headers["Idempotency-Key"]).not.toBe(keys[0]);
  });

  it("처리 중 재클릭(더블클릭)은 요청을 한 번만 보낸다", async () => {
    let release: (r: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = open(detail(), []);
    // 응답을 늦추는 스텁으로 교체(발행 POST만 보류).
    const base = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn((u: string, i?: RequestInit) => (u.endsWith("/issue") ? (calls.push({ url: u, method: "POST", body: null, headers: {} }), pending) : base(u, i))));
    fireEvent.click(await screen.findByRole("button", { name: "발행" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "발행" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(calls.filter((c) => c.url.endsWith("/issue"))).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((c) => c.url.endsWith("/issue"))).toHaveLength(1);
    expect(within(dialog).getByRole("button", { name: "처리 중…" })).toBeDisabled();
    release(jsonResponse(detail({ status: "ISSUED", frozen_at: FROZEN_AT, version: 5 })));
  });
});

describe("견적 상세 — 다이얼로그 409", () => {
  it.each([
    ["발행", "발행", "/v1/quotations/7/issue", "발행"],
    ["취소", "취소 확정", "/v1/quotations/7/transitions", "취소"],
    ["개정본 작성", "개정본 작성", "/v1/quotations/7/revisions", "개정본 작성"],
  ])("%s 409는 다이얼로그 안에 '최신 내용 불러오기'를 두고, 누르면 재조회 후 닫힌다", async (open_, confirmName, path) => {
    const status = open_ === "개정본 작성" ? "ISSUED" : "DRAFT";
    const { calls } = open(detail({ status, frozen_at: status === "ISSUED" ? FROZEN_AT : null }), [
      [path, "POST", CONFLICT_RESPONSE],
    ]);
    fireEvent.click(await screen.findByRole("button", { name: open_ }));
    const dialog = await screen.findByRole("dialog");
    if (open_ === "취소") fireEvent.change(within(dialog).getByLabelText("취소 사유 (필수)"), { target: { value: "사유" } });
    fireEvent.click(within(dialog).getByRole("button", { name: confirmName }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("다른 곳에서 이 견적이 먼저 수정되었습니다");
    const before = calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length;
    fireEvent.click(within(dialog).getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "GET" && c.url.endsWith("/v1/quotations/7")).length).toBeGreaterThan(before),
    );
  });
});

describe("견적 상세 — 창 포커스 재조회·동결 전환·미저장", () => {
  it("창 포커스 재조회로 서버 version이 앞서가도 저장은 폼이 본 version으로 나가 409로 막힌다", async () => {
    const { calls } = open(detail(), [["/v1/quotations/7", "PATCH", CONFLICT_RESPONSE]]);
    await screen.findByRole("form", { name: "견적 헤더 편집" });
    server.qt = detail({ version: 5, internal_note: "남이 고침" });
    window.dispatchEvent(new Event("visibilitychange"));
    expect(await screen.findByText(/다른 곳에서 이 견적이 수정되었습니다/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "헤더 저장" }));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH" && c.url.endsWith("/v1/quotations/7"));
      expect(patch?.body).toMatchObject({ version: 4 }); // 5가 아니다
    });
  });

  it("편집 중 견적이 동결되면(최신 불러오기) 라인 수정 폼이 사라진다", async () => {
    open(detail());
    fireEvent.click(await screen.findByRole("button", { name: "수정" }));
    expect(screen.getByRole("form", { name: "라인 1 수정" })).toBeInTheDocument();
    server.qt = detail({ status: "ISSUED", frozen_at: FROZEN_AT, version: 5 });
    fireEvent.click(screen.getByRole("button", { name: "최신 내용 불러오기" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "라인 1 수정" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "수정" })).not.toBeInTheDocument();
  });

  it("미저장 헤더 변경이 있으면 발행 버튼이 잠기고 안내가 뜬다", async () => {
    open(detail());
    const form = await screen.findByRole("form", { name: "견적 헤더 편집" });
    expect(screen.getByRole("button", { name: "발행" })).toBeEnabled();
    fireEvent.change(within(form).getByLabelText("서류상 바이어명"), { target: { value: "다른 이름" } });
    expect(screen.getByRole("button", { name: "발행" })).toBeDisabled();
    expect(screen.getByText(/저장하지 않은 헤더 변경이 있습니다/)).toBeInTheDocument();
  });

  it("증빙일을 비우면 저장이 막히고 필수 안내가 뜬다", async () => {
    const { calls } = open(detail());
    const form = await screen.findByRole("form", { name: "견적 헤더 편집" });
    fireEvent.change(within(form).getByLabelText("증빙일"), { target: { value: "" } });
    expect(within(form).getByRole("button", { name: "헤더 저장" })).toBeDisabled();
    expect(within(form).getByRole("alert")).toHaveTextContent("증빙일은 필수입니다.");
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });
});
