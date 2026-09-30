// QT/PI → SO 만들기 다이얼로그 (S3-1 PR-7b — design-A A4 참조 생성 2단 / design-B B8).
//
// SO는 접수(RECEIVED)가 편집 가능 초안이라 서버에 미리보기 엔드포인트가 없다(조건·환율·단가 조정은 생성 후 편집) —
// 그래서 ① 입력(바이어 PO·증빙일·라인·수량) → ② '확인' 단계(보낼 내용 요약, 같은 본문 고정) → ③ '생성 확정' 순서로 둔다.
// 규칙:
// - 요청 본문에는 원천(QT·PI)에 있는 값(SKU·단가·통화·환율·바이어)이 없다 — 재입력 화면이 없다(서버 스키마가 구조로 보증).
// - 원천 version은 부모가 넘긴 기준 version(baseVersion)이다. 서버 409면 다이얼로그 안에서 '최신 내용 불러오기'.
// - 멱등 키는 다이얼로그를 여는 순간 1개 — 결과를 모르는 실패 뒤 같은 본문 재확정은 같은 키, 본문이 실제로 달라질 때만 새 키.
// - 더블클릭은 동기 잠금(ref)으로 1회만 전송. 확정은 확인 단계 때의 본문 그대로 보낸다.
// - 수량은 원천 라인 수량 이내(형식·상한만 화면 검증) — 다른 수주가 가져간 몫(잔량)은 서버만 안다. 초과는 서버 409를 라인별 잔량으로 보여 준다.
// - 바이어 PO번호는 선택(서버가 미기재를 허용) — 중복이면 서버 409와 점유 수주를 안내한다.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useDialogBehavior } from "../components/confirm-dialog";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import { todayKst } from "../lib/datetime";
import { PROFORMAS_QUERY_KEY, QUANTITY_EXCEEDS_OPEN_CODE } from "../lib/proforma";
import { QUOTATIONS_QUERY_KEY } from "../lib/quotation";
import {
  DOCUMENT_FLOW_QUERY_KEY,
  SALES_ORDERS_QUERY_KEY,
  type SalesOrderCreateBody,
  type SalesOrderDetail,
} from "../lib/sales-order";
import { occupantNotice } from "../lib/sales-order-errors";

const MAX_QUANTITY = 99_999_999; // 서버 MAX_QUANTITY와 같은 상한 — 형식 검증용
const MAX_PO_LENGTH = 60; // 서버 정규화 후 PO번호 상한

export interface SourceLine {
  id: number;
  line_no: number;
  sku_code: string;
  sku_name_ko: string;
  quantity: number;
}

export interface SoSource {
  kind: "QUOTATION" | "PROFORMA_INVOICE";
  id: number;
  doc_number: string;
  /** 원천 증빙일 — SO 증빙일은 이보다 앞설 수 없다(서버도 검사). */
  doc_date: string;
  lines: SourceLine[];
}

interface Pick {
  checked: boolean;
  qty: string;
  delivery: string;
}

interface Props {
  source: SoSource;
  /** 화면이 본 기준 version(baseVersion) — 서버 version이 앞서가도 이 값으로 보내 409가 덮어쓰기를 막는다. */
  version: number;
  onClose: () => void;
  /** 409(원천이 그 사이 바뀜) 시 '최신 내용 불러오기' — 부모가 닫고 재조회한다. */
  onReload: () => void;
}

const SOURCE_NOUN: Record<SoSource["kind"], string> = { QUOTATION: "견적", PROFORMA_INVOICE: "PI" };
const SOURCE_PATH: Record<SoSource["kind"], string> = {
  QUOTATION: "quotations",
  PROFORMA_INVOICE: "proforma-invoices",
};

/** 잔량 초과 409의 라인별 잔량 안내 — detail.open_quantity {원천 라인 id: 잔량}. */
export function openQuantityHints(error: unknown, lines: SourceLine[]): string[] {
  if (!(error instanceof ApiError) || error.code !== QUANTITY_EXCEEDS_OPEN_CODE) return [];
  const map = error.detail.open_quantity;
  if (typeof map !== "object" || map === null) return [];
  return Object.entries(map as Record<string, unknown>).map(([lineId, open]) => {
    const line = lines.find((item) => String(item.id) === lineId);
    return `${line ? `라인 ${line.line_no} (${line.sku_code})` : `라인 #${lineId}`}: 남은 수량 ${String(open)}`;
  });
}

export function SalesOrderCreateDialog({ source, version, onClose, onReload }: Props) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const noun = SOURCE_NOUN[source.kind];

  const [docDate, setDocDate] = useState("");
  const [poNo, setPoNo] = useState("");
  const [poDate, setPoDate] = useState("");
  const [note, setNote] = useState("");
  const [mode, setMode] = useState<"ALL" | "CUSTOM">("ALL");
  const [picks, setPicks] = useState<Record<number, Pick>>(() =>
    Object.fromEntries(source.lines.map((line) => [line.id, { checked: true, qty: String(line.quantity), delivery: "" }])),
  );
  const [formError, setFormError] = useState<string | null>(null);
  // 확인 단계로 넘어갈 때 고정한 본문 — 확정은 이 본문 그대로 보낸다(화면 입력이 그 뒤 바뀌어도 다른 것을 만들지 않는다).
  const [review, setReview] = useState<SalesOrderCreateBody | null>(null);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const createLock = useRef(false);
  // 멱등 키는 다이얼로그를 여는 순간 1개. 새 키는 "마지막으로 서버에 보낸 본문과 실제로 다른 본문"을 보낼 때만 —
  // 결과를 모르는 실패(네트워크 오류) 뒤 입력을 바꿨다 되돌려 재확정하면 같은 키여야 중복 SO가 안 생긴다.
  const [initialKey] = useState(() => crypto.randomUUID());
  const keyRef = useRef(initialKey);
  const lastSent = useRef<string | null>(null);

  const createMutation = useMutation({
    mutationFn: (body: SalesOrderCreateBody) =>
      apiFetch<SalesOrderDetail>(`/v1/${SOURCE_PATH[source.kind]}/${source.id}/sales-orders`, {
        method: "POST",
        idempotencyKey: keyRef.current,
        body,
      }),
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: SALES_ORDERS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: PROFORMAS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
      void navigate(`/sales-orders/${created.id}`);
    },
  });

  const busy = createMutation.isPending;
  useDialogBehavior(boxRef, () => {
    if (!busy) onClose();
  });

  function touched() {
    setFormError(null);
    createMutation.reset();
  }

  function buildBody(): SalesOrderCreateBody | null {
    const po = poNo.trim();
    if (po.length > MAX_PO_LENGTH) {
      setFormError(`바이어 PO번호는 ${MAX_PO_LENGTH}자 이하로 입력해 주세요.`);
      return null;
    }
    if (poDate !== "" && poDate > todayKst()) {
      setFormError("바이어 PO 일자는 오늘보다 미래일 수 없습니다.");
      return null;
    }
    if (docDate !== "" && docDate < source.doc_date) {
      setFormError(`증빙일은 원천 ${noun}의 증빙일(${source.doc_date})보다 앞설 수 없습니다.`);
      return null;
    }
    const body: SalesOrderCreateBody = { version };
    if (docDate !== "") body.doc_date = docDate;
    if (po !== "") body.buyer_po_no = po;
    if (poDate !== "") body.buyer_po_date = poDate;
    if (note.trim() !== "") body.internal_note = note.trim();
    if (mode === "CUSTOM") {
      const lines: NonNullable<SalesOrderCreateBody["lines"]> = [];
      for (const line of source.lines) {
        const pick = picks[line.id];
        if (!pick?.checked) continue;
        const qty = pick.qty.trim();
        if (!/^[1-9][0-9]*$/.test(qty) || Number(qty) > MAX_QUANTITY) {
          setFormError(`라인 ${line.line_no}(${line.sku_code})의 수량은 1 이상의 정수로 입력해 주세요.`);
          return null;
        }
        if (Number(qty) > line.quantity) {
          setFormError(
            `라인 ${line.line_no}(${line.sku_code})의 수량은 원천 ${noun} 수량(${line.quantity})을 넘을 수 없습니다.`,
          );
          return null;
        }
        const entry: NonNullable<SalesOrderCreateBody["lines"]>[number] = {
          source_line_id: line.id,
          quantity: Number(qty),
        };
        if (pick.delivery !== "") entry.requested_delivery_date = pick.delivery;
        lines.push(entry);
      }
      if (lines.length === 0) {
        setFormError("가져올 라인을 하나 이상 선택해 주세요.");
        return null;
      }
      body.lines = lines;
    }
    return body;
  }

  function goReview() {
    setFormError(null);
    const body = buildBody();
    if (body === null) return;
    createMutation.reset();
    setReview(body);
  }

  function confirmCreate() {
    if (review === null || createLock.current) return;
    createLock.current = true; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
    const serialized = JSON.stringify(review);
    if (lastSent.current !== null && lastSent.current !== serialized) keyRef.current = crypto.randomUUID();
    lastSent.current = serialized;
    createMutation.mutate(review, { onSettled: () => (createLock.current = false) });
  }

  const activeError = createMutation.error;
  const conflict = isVersionConflict(activeError);
  const hints = openQuantityHints(activeError, source.lines);
  const occupant = occupantNotice(activeError);

  const errorBlock = (activeError || formError) && (
    <div role="alert" className="mt-3 text-sm text-signal-red">
      <p className="break-keep">
        {formError ??
          (occupant !== null && activeError instanceof ApiError
            ? activeError.message
            : errorMessage(activeError, "수주를 만들지 못했습니다.", noun))}
      </p>
      {occupant !== null && <p className="mt-1 break-keep font-medium">{occupant.text}</p>}
      {hints.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {hints.map((hint) => (
            <li key={hint} className="break-keep">
              {hint}
            </li>
          ))}
        </ul>
      )}
      {conflict && (
        <button
          type="button"
          onClick={onReload}
          className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1"
        >
          최신 내용 불러오기
        </button>
      )}
    </div>
  );

  // 전체(ALL) 모드는 본문에 lines가 없다 — 확인 표에는 원천 라인을 '남은 수량 전부'로 보인다.
  const reviewRows: Array<{ source_line_id: number; quantity: number | null; requested_delivery_date?: string }> =
    review?.lines ?? source.lines.map((line) => ({ source_line_id: line.id, quantity: null }));

  const setPick = (lineId: number, patch: Partial<Pick>) => {
    touched();
    setPicks((prev) => ({ ...prev, [lineId]: { ...prev[lineId]!, ...patch } }));
  };

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label="수주 만들기"
        className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg bg-white p-5 shadow-lg"
      >
        {review === null ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              goReview();
            }}
          >
            <h2 className="text-lg font-bold">수주 만들기 — {source.doc_number}</h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              {noun}의 가격·환율·조건·바이어가 그대로 복사됩니다(다시 입력하지 않습니다). 만들면 <strong>접수</strong> 상태로
              시작하고, 조건·단가는 수주 상세에서 고칠 수 있습니다.
            </p>

            <div className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              <div className="flex flex-col gap-1">
                <label className="flex flex-col gap-1">
                  <span className="text-gray-600">바이어 PO번호 (선택)</span>
                  <input
                    value={poNo}
                    maxLength={200}
                    aria-describedby="so-po-hint"
                    onChange={(event) => {
                      touched();
                      setPoNo(event.target.value);
                    }}
                    className="rounded border border-gray-300 px-3 py-2"
                  />
                </label>
                <span id="so-po-hint" className="break-keep text-xs text-gray-500">
                  같은 바이어의 같은 PO번호는 한 번만 등록됩니다(취소한 수주는 제외).
                </span>
              </div>
              <label className="flex flex-col gap-1">
                <span className="text-gray-600">바이어 PO 일자 (선택)</span>
                <input
                  type="date"
                  value={poDate}
                  onChange={(event) => {
                    touched();
                    setPoDate(event.target.value);
                  }}
                  className="rounded border border-gray-300 px-3 py-2"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-gray-600">증빙일 (비우면 오늘)</span>
                <input
                  type="date"
                  value={docDate}
                  onChange={(event) => {
                    touched();
                    setDocDate(event.target.value);
                  }}
                  className="rounded border border-gray-300 px-3 py-2"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-gray-600">내부 메모 (선택)</span>
                <input
                  value={note}
                  maxLength={1000}
                  onChange={(event) => {
                    touched();
                    setNote(event.target.value);
                  }}
                  className="rounded border border-gray-300 px-3 py-2"
                />
              </label>
            </div>

            <fieldset className="mt-4 text-sm">
              <legend className="text-gray-600">가져올 라인</legend>
              <div className="mt-1 flex flex-wrap gap-4">
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="so-line-mode"
                    checked={mode === "ALL"}
                    onChange={() => {
                      touched();
                      setMode("ALL");
                    }}
                  />
                  <span>전체 (각 라인 남은 수량 전부)</span>
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="so-line-mode"
                    checked={mode === "CUSTOM"}
                    onChange={() => {
                      touched();
                      setMode("CUSTOM");
                    }}
                  />
                  <span>일부 라인·수량 선택</span>
                </label>
              </div>
            </fieldset>

            {mode === "CUSTOM" && (
              <div className="mt-3 overflow-x-auto rounded border border-gray-200">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-gray-600">
                    <tr>
                      <th className="cell-nowrap px-3 py-2 text-center">선택</th>
                      <th className="cell-nowrap px-3 py-2 text-center">번호</th>
                      <th className="cell-nowrap px-3 py-2">SKU</th>
                      <th className="cell-nowrap px-3 py-2">품명</th>
                      <th className="cell-nowrap px-3 py-2 text-center">{noun} 수량</th>
                      <th className="cell-nowrap px-3 py-2 text-center">수주 수량</th>
                      <th className="cell-nowrap px-3 py-2 text-center">요청납기</th>
                    </tr>
                  </thead>
                  <tbody>
                    {source.lines.map((line) => {
                      const pick = picks[line.id]!;
                      return (
                        <tr key={line.id} className="border-t border-gray-100">
                          <td className="px-3 py-2 text-center">
                            <input
                              type="checkbox"
                              aria-label={`라인 ${line.line_no} 선택`}
                              checked={pick.checked}
                              onChange={(event) => setPick(line.id, { checked: event.target.checked })}
                            />
                          </td>
                          <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                          <td className="cell-nowrap px-3 py-2">{line.sku_code}</td>
                          <td className="break-keep px-3 py-2">{line.sku_name_ko}</td>
                          <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                          <td className="px-3 py-2 text-center">
                            <input
                              inputMode="numeric"
                              aria-label={`라인 ${line.line_no} 수량`}
                              value={pick.qty}
                              disabled={!pick.checked}
                              onChange={(event) => setPick(line.id, { qty: event.target.value })}
                              className="w-24 rounded border border-gray-300 px-2 py-1 text-center disabled:bg-gray-100"
                            />
                          </td>
                          <td className="px-3 py-2 text-center">
                            <input
                              type="date"
                              aria-label={`라인 ${line.line_no} 요청납기`}
                              value={pick.delivery}
                              disabled={!pick.checked}
                              onChange={(event) => setPick(line.id, { delivery: event.target.value })}
                              className="rounded border border-gray-300 px-2 py-1 disabled:bg-gray-100"
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {errorBlock}

            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={onClose} className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm">
                닫기
              </button>
              <button type="submit" className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white">
                다음: 내용 확인
              </button>
            </div>
          </form>
        ) : (
          <div>
            <h2 className="text-lg font-bold">수주를 만들까요? — {source.doc_number}</h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              아래 내용으로 <strong>접수</strong> 상태의 수주가 만들어지고 수주번호가 붙습니다. 가격·환율·조건·바이어는 {noun}에서
              복사됩니다.
            </p>
            <dl className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="cell-nowrap text-xs text-gray-500">바이어 PO번호</dt>
                <dd>{review.buyer_po_no ?? "—"}</dd>
              </div>
              <div>
                <dt className="cell-nowrap text-xs text-gray-500">바이어 PO 일자</dt>
                <dd>{review.buyer_po_date ?? "—"}</dd>
              </div>
              <div>
                <dt className="cell-nowrap text-xs text-gray-500">증빙일</dt>
                <dd>{review.doc_date ?? "오늘 (서버 기준)"}</dd>
              </div>
              <div>
                <dt className="cell-nowrap text-xs text-gray-500">내부 메모</dt>
                <dd className="break-keep">{review.internal_note ?? "—"}</dd>
              </div>
            </dl>
            <div className="mt-3 overflow-x-auto rounded border border-gray-200">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left text-gray-600">
                  <tr>
                    <th className="cell-nowrap px-3 py-2 text-center">번호</th>
                    <th className="cell-nowrap px-3 py-2">SKU</th>
                    <th className="cell-nowrap px-3 py-2">품명</th>
                    <th className="cell-nowrap px-3 py-2 text-center">수주 수량</th>
                    <th className="cell-nowrap px-3 py-2 text-center">요청납기</th>
                  </tr>
                </thead>
                <tbody>
                  {reviewRows.map((entry) => {
                    const line = source.lines.find((item) => item.id === entry.source_line_id);
                    return (
                      <tr key={entry.source_line_id} className="border-t border-gray-100">
                        <td className="num cell-nowrap px-3 py-2">{line?.line_no ?? "—"}</td>
                        <td className="cell-nowrap px-3 py-2">{line?.sku_code ?? `#${entry.source_line_id}`}</td>
                        <td className="break-keep px-3 py-2">{line?.sku_name_ko ?? ""}</td>
                        <td className="num cell-nowrap px-3 py-2">{entry.quantity ?? "남은 수량 전부"}</td>
                        <td className="num cell-nowrap px-3 py-2">{entry.requested_delivery_date ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {errorBlock}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  createMutation.reset();
                  setReview(null);
                }}
                className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
              >
                뒤로
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={confirmCreate}
                className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {busy ? "만드는 중…" : "생성 확정"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
