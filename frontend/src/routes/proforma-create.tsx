// QT → PI 만들기 다이얼로그 (S3-1 PR-6b — design-A A4 참조 생성 / 미리보기 먼저, 확정은 그다음).
//
// 흐름: ① 입력(은행계좌·증빙일·유효기간·라인·수량) → ② `/preview`(저장 안 됨 — 번호·이벤트·멱등 키 소비 없음)를 서버 계산값 그대로 보여 줌
//       → ③ '생성 확정'(같은 본문으로 POST, 성공하면 PI 상세로 이동).
// 규칙:
// - 요청 본문에는 원천 QT에 있는 값(SKU·단가·통화·환율·바이어)이 없다 — 재입력 화면이 없다(서버 스키마가 구조로 보증).
// - 원천 QT version은 부모가 넘긴 기준 version(baseVersion)이다. 서버 409면 다이얼로그 안에서 '최신 내용 불러오기'.
// - 멱등 키는 다이얼로그를 여는 순간 1개 — 재시도·더블클릭은 같은 키, 입력이 바뀌면(=본문이 달라지면) 새 키.
// - 더블클릭은 동기 잠금(ref)으로 1회만 전송. 확정은 **미리보기 때의 본문 그대로** 보낸다(미리보기와 다른 것을 만들지 않는다).
// - 수량은 잔량 이내 — 잔량은 서버만 안다(다른 PI가 가져간 몫). 화면은 형식만 검증하고 초과는 서버 409를 라인별 잔량으로 보여 준다.
// - 금액·선수금은 서버 문자열 그대로(산술 0).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { useDialogBehavior } from "../components/confirm-dialog";
import {
  AdvanceView,
  BankSnapshotView,
  DocField,
  EMPTY,
  incotermText,
  paymentTermsText,
  show,
} from "../components/proforma-facts";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import { usePagedQuery } from "../lib/paging";
import {
  BANK_ACCOUNTS_QUERY_KEY,
  PROFORMAS_QUERY_KEY,
  QUANTITY_EXCEEDS_OPEN_CODE,
  type BankAccount,
  type ProformaCreateBody,
  type ProformaDetail,
  type ProformaPreview,
} from "../lib/proforma";
import { QUOTATIONS_QUERY_KEY, type QuotationDetail } from "../lib/quotation";
import { hasRole, useSession } from "../lib/session";

const MAX_QUANTITY = 99_999_999; // 서버 MAX_QUANTITY와 같은 상한 — 형식 검증용

interface Pick {
  checked: boolean;
  qty: string;
}

interface Props {
  qt: QuotationDetail;
  /** 화면이 본 기준 version(baseVersion) — 서버 version이 앞서가도 이 값으로 보내 409가 덮어쓰기를 막는다. */
  version: number;
  onClose: () => void;
  /** 409(원천 견적이 그 사이 바뀜) 시 '최신 내용 불러오기' — 부모가 닫고 재조회한다. */
  onReload: () => void;
}

/** 잔량 초과 409의 라인별 잔량 안내 — detail.open_quantity {원천 라인 id: 잔량}. */
function openQuantityHints(error: unknown, qt: QuotationDetail): string[] {
  if (!(error instanceof ApiError) || error.code !== QUANTITY_EXCEEDS_OPEN_CODE) return [];
  const map = error.detail.open_quantity;
  if (typeof map !== "object" || map === null) return [];
  return Object.entries(map as Record<string, unknown>).map(([lineId, open]) => {
    const line = qt.lines.find((item) => String(item.id) === lineId);
    return `${line ? `라인 ${line.line_no} (${line.sku_code})` : `라인 #${lineId}`}: 남은 수량 ${String(open)}`;
  });
}

export function ProformaCreateDialog({ qt, version, onClose, onReload }: Props) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { me } = useSession();
  const isAdmin = hasRole(me);
  const banks = usePagedQuery<BankAccount>(
    [...BANK_ACCOUNTS_QUERY_KEY, "select", qt.currency],
    `/v1/bank-accounts?currency=${encodeURIComponent(qt.currency)}&size=200`,
  );

  const [docDate, setDocDate] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [bankId, setBankId] = useState("");
  const [mode, setMode] = useState<"ALL" | "CUSTOM">("ALL");
  const [picks, setPicks] = useState<Record<number, Pick>>(() =>
    Object.fromEntries(qt.lines.map((line) => [line.id, { checked: true, qty: String(line.quantity) }])),
  );
  const [note, setNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ data: ProformaPreview; body: ProformaCreateBody } | null>(null);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const previewLock = useRef(false);
  const createLock = useRef(false);
  // 멱등 키는 다이얼로그를 여는 순간 1개 — 입력이 바뀌면 새 키(본문이 달라졌으므로).
  const keyRef = useRef(crypto.randomUUID());
  const rekey = () => {
    keyRef.current = crypto.randomUUID();
  };

  const previewMutation = useMutation({
    mutationFn: (body: ProformaCreateBody) =>
      apiFetch<ProformaPreview>(`/v1/quotations/${qt.id}/proforma-invoices/preview`, {
        method: "POST",
        body,
      }),
    onSuccess: (data, body) => setPreview({ data, body }),
  });

  const createMutation = useMutation({
    mutationFn: (body: ProformaCreateBody) =>
      apiFetch<ProformaDetail>(`/v1/quotations/${qt.id}/proforma-invoices`, {
        method: "POST",
        idempotencyKey: keyRef.current,
        body,
      }),
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: PROFORMAS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: QUOTATIONS_QUERY_KEY });
      void navigate(`/proforma-invoices/${created.id}`);
    },
  });

  const busy = previewMutation.isPending || createMutation.isPending;
  useDialogBehavior(boxRef, () => {
    if (!busy) onClose();
  });

  /** 입력이 바뀌면 이전 시도의 오류·미리보기를 버리고 새 키를 쓴다. */
  function touched() {
    rekey();
    setFormError(null);
    previewMutation.reset();
    createMutation.reset();
  }

  function buildBody(): ProformaCreateBody | null {
    if (validUntil === "") {
      setFormError("유효기간을 입력해 주세요.");
      return null;
    }
    if (docDate !== "" && validUntil < docDate) {
      setFormError("유효기간은 증빙일보다 앞설 수 없습니다.");
      return null;
    }
    if (bankId === "") {
      setFormError("입금 은행 계좌를 선택해 주세요.");
      return null;
    }
    const body: ProformaCreateBody = {
      version,
      valid_until: validUntil,
      bank_account_id: Number(bankId),
    };
    if (docDate !== "") body.doc_date = docDate;
    if (note.trim() !== "") body.overrides = { internal_note: note.trim() };
    if (mode === "CUSTOM") {
      const lines: NonNullable<ProformaCreateBody["lines"]> = [];
      for (const line of qt.lines) {
        const pick = picks[line.id];
        if (!pick?.checked) continue;
        const qty = pick.qty.trim();
        if (!/^[1-9][0-9]*$/.test(qty) || Number(qty) > MAX_QUANTITY) {
          setFormError(`라인 ${line.line_no}(${line.sku_code})의 수량은 1 이상의 정수로 입력해 주세요.`);
          return null;
        }
        lines.push({ source_line_id: line.id, quantity: Number(qty) });
      }
      if (lines.length === 0) {
        setFormError("가져올 라인을 하나 이상 선택해 주세요.");
        return null;
      }
      body.lines = lines;
    }
    return body;
  }

  function submitPreview() {
    if (previewLock.current) return;
    setFormError(null);
    const body = buildBody();
    if (body === null) return;
    previewLock.current = true; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
    previewMutation.mutate(body, { onSettled: () => (previewLock.current = false) });
  }

  function confirmCreate() {
    if (preview === null || createLock.current) return;
    createLock.current = true;
    // ★ 미리보기 때의 본문 그대로 — 화면 입력이 그사이 바뀌었어도(뒤로 갔다면 미리보기가 버려진다) 다른 것을 만들지 않는다.
    createMutation.mutate(preview.body, { onSettled: () => (createLock.current = false) });
  }

  const activeError = createMutation.error ?? previewMutation.error;
  const conflict = isVersionConflict(activeError);
  const hints = openQuantityHints(activeError, qt);
  const bankItems = banks.data?.items ?? [];

  const errorBlock = (activeError || formError) && (
    <div role="alert" className="mt-3 text-sm text-signal-red">
      <p className="break-keep">{formError ?? errorMessage(activeError, "PI를 처리하지 못했습니다.", "견적")}</p>
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

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-label="PI 만들기"
        className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg bg-white p-5 shadow-lg"
      >
        {preview === null ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              submitPreview();
            }}
          >
            <h2 className="text-lg font-bold">PI 만들기 — {qt.doc_number}</h2>
            <p className="mt-1 break-keep text-sm text-gray-500">
              견적의 가격·환율·조건·바이어가 그대로 복사됩니다(다시 입력하지 않습니다). 먼저 <strong>미리보기</strong>로
              결과를 확인하고, 확정해야 PI번호가 붙습니다.
            </p>

            <div className="mt-4 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
              <label className="flex flex-col gap-1">
                <span className="text-gray-600">입금 은행 계좌 ({qt.currency}) *</span>
                <select
                  value={bankId}
                  onChange={(event) => {
                    touched();
                    setBankId(event.target.value);
                  }}
                  className="rounded border border-gray-300 px-3 py-2"
                >
                  <option value="">선택하세요</option>
                  {bankItems.map((bank) => (
                    <option key={bank.id} value={bank.id}>
                      {bank.label} — {bank.bank_name}
                    </option>
                  ))}
                </select>
                {banks.isPending && <span className="text-xs text-gray-500">계좌를 불러오는 중…</span>}
                {banks.error && (
                  <span role="alert" className="text-xs text-signal-red">
                    {errorMessage(banks.error, "은행 계좌를 불러오지 못했습니다.")}
                  </span>
                )}
                {banks.data && bankItems.length === 0 && (
                  <span role="status" className="break-keep text-xs text-signal-red">
                    {qt.currency} 계좌가 없습니다. {isAdmin ? "은행 계좌 메뉴에서 먼저 등록하세요." : "관리자에게 계좌 등록을 요청하세요."}
                  </span>
                )}
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-gray-600">유효기간 *</span>
                <input
                  type="date"
                  value={validUntil}
                  onChange={(event) => {
                    touched();
                    setValidUntil(event.target.value);
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
                    name="pi-line-mode"
                    checked={mode === "ALL"}
                    onChange={() => {
                      touched();
                      setMode("ALL");
                    }}
                  />
                  <span>남은 수량 전부 (기본)</span>
                </label>
                <label className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="pi-line-mode"
                    checked={mode === "CUSTOM"}
                    onChange={() => {
                      touched();
                      setMode("CUSTOM");
                    }}
                  />
                  <span>라인·수량 직접 지정</span>
                </label>
              </div>
              {mode === "CUSTOM" && (
                <div className="mt-2 overflow-x-auto rounded border border-gray-200">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 text-left text-gray-600">
                      <tr>
                        <th className="cell-nowrap px-3 py-2 text-center">선택</th>
                        <th className="cell-nowrap px-3 py-2 text-center">번호</th>
                        <th className="cell-nowrap px-3 py-2">SKU</th>
                        <th className="cell-nowrap px-3 py-2">품명</th>
                        <th className="cell-nowrap px-3 py-2 text-center">견적 수량</th>
                        <th className="cell-nowrap px-3 py-2 text-center">가져올 수량</th>
                      </tr>
                    </thead>
                    <tbody>
                      {qt.lines.map((line) => {
                        const pick = picks[line.id] ?? { checked: false, qty: "" };
                        return (
                          <tr key={line.id} className="border-t border-gray-100">
                            <td className="px-3 py-2 text-center">
                              <input
                                type="checkbox"
                                aria-label={`라인 ${line.line_no} 선택`}
                                checked={pick.checked}
                                onChange={(event) => {
                                  touched();
                                  setPicks({ ...picks, [line.id]: { ...pick, checked: event.target.checked } });
                                }}
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
                                onChange={(event) => {
                                  touched();
                                  setPicks({ ...picks, [line.id]: { ...pick, qty: event.target.value } });
                                }}
                                className="num w-24 rounded border border-gray-300 px-2 py-1 disabled:bg-gray-100"
                              />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <p className="mt-1 break-keep text-xs text-gray-500">
                수량은 이 견적에서 다른 PI가 이미 가져가고 남은 수량 이내여야 합니다. 남은 수량은 서버가 계산해 미리보기에
                보여 줍니다.
              </p>
            </fieldset>

            {errorBlock}

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm"
              >
                닫기
              </button>
              <button
                type="submit"
                disabled={previewMutation.isPending}
                className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
              >
                {previewMutation.isPending ? "계산 중…" : "미리보기"}
              </button>
            </div>
          </form>
        ) : (
          <PreviewPanel
            data={preview.data}
            pending={createMutation.isPending}
            errorBlock={errorBlock}
            onBack={() => {
              // 수정하러 돌아간다 — 본문이 달라질 수 있으니 미리보기·오류를 버리고 새 키.
              setPreview(null);
              touched();
            }}
            onConfirm={confirmCreate}
            onClose={onClose}
          />
        )}
      </div>
    </div>
  );
}

function PreviewPanel({
  data,
  pending,
  errorBlock,
  onBack,
  onConfirm,
  onClose,
}: {
  data: ProformaPreview;
  pending: boolean;
  errorBlock: ReactNode;
  onBack: () => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <div>
      <h2 className="text-lg font-bold">PI 미리보기 — {data.qt_doc_number}</h2>
      <p role="status" className="mt-1 break-keep rounded border border-gray-400 p-2 text-sm">
        아직 저장되지 않았습니다. <strong>'생성 확정'</strong>을 누르면 PI번호가 붙고 바로 발행·동결되어 가격·조건을 고칠 수
        없습니다(잘못되면 취소하고 새로 만듭니다).
      </p>

      <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <DocField label="바이어">{data.buyer_name}</DocField>
        <DocField label="목적지">{data.dest_market_code}</DocField>
        <DocField label="통화">{data.currency}</DocField>
        <DocField label="증빙일">{data.doc_date}</DocField>
        <DocField label="유효기간">{data.valid_until}</DocField>
        <DocField label="환율">
          {data.fx_rate === null ? EMPTY : `${data.fx_rate} (기준일 ${show(data.fx_rate_date)})`}
        </DocField>
        <DocField label="결제조건">{paymentTermsText(data.payment_terms)}</DocField>
        <DocField label="인코텀즈">{incotermText(data.incoterm)}</DocField>
        <DocField label="바이어 주소">{show(data.buyer_address)}</DocField>
      </dl>

      <div className="mt-4">
        <BankSnapshotView bank={data.bank} />
      </div>

      <div className="mt-4 overflow-x-auto rounded-lg border border-gray-200">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th className="cell-nowrap px-3 py-2 text-center">번호</th>
              <th className="cell-nowrap px-3 py-2">SKU</th>
              <th className="cell-nowrap px-3 py-2">품명</th>
              <th className="cell-nowrap px-3 py-2 text-center">수량</th>
              <th className="cell-nowrap px-3 py-2 text-center">남은 수량</th>
              <th className="cell-nowrap px-3 py-2 text-center">단가</th>
              <th className="cell-nowrap px-3 py-2 text-center">금액</th>
            </tr>
          </thead>
          <tbody>
            {data.lines.map((line) => (
              <tr key={line.qt_line_id} className="border-t border-gray-100 align-top">
                <td className="num cell-nowrap px-3 py-2">{line.line_no}</td>
                <td className="cell-nowrap px-3 py-2">{line.sku_code}</td>
                <td className="break-keep px-3 py-2">{line.sku_name_ko}</td>
                <td className="num cell-nowrap px-3 py-2">{line.quantity}</td>
                <td className="num cell-nowrap px-3 py-2">{line.open_quantity_before}</td>
                <td className="num cell-nowrap px-3 py-2">{line.is_free ? "무상" : line.unit_price_text}</td>
                <td className="num cell-nowrap px-3 py-2">{line.line_amount_text}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t border-gray-200 bg-gray-50 font-semibold">
              <td colSpan={6} className="cell-nowrap px-3 py-2 text-right">
                합계 (서버 계산)
              </td>
              <td className="num cell-nowrap px-3 py-2">
                {data.total_text} {data.currency}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      <div className="mt-3">
        <AdvanceView advance={data.advance} currency={data.currency} />
      </div>

      {errorBlock}

      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          disabled={pending}
          className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
        >
          닫기
        </button>
        <button
          type="button"
          onClick={onBack}
          disabled={pending}
          className="cell-nowrap rounded border border-gray-900 px-4 py-2 text-sm disabled:opacity-50"
        >
          입력 수정
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={pending}
          className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {pending ? "만드는 중…" : "생성 확정"}
        </button>
      </div>
    </div>
  );
}
