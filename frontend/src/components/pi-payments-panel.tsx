// PI 입금 패널 (S3-1 PR-10b — design-E E7 / E10). 선수금 입금 기록·역기록·입금 내역.
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - 금액은 서버 문자열(*_text)만 표시한다. 프런트 산술 0. 통화는 PI 통화로 고정(환산 없음).
// - 입금은 되돌릴 수 없다 — 정정은 '역기록 후 재입금'(원본 불변·반대 부호 신규 기록). 확인 다이얼로그에서 안내한다.
// - 멱등 키: 본문(JSON) 기준 Map으로 재사용(같은 본문 재시도=같은 키), 성공하면 Map을 비운다(같은 금액의 다음 입금이 이전 응답을 재생하지 않게).
// - 더블클릭: 동기 잠금(ref)을 mutationFn의 finally에서 푼다 — mutate 단위 onSettled는 요청 중 reset()·언마운트 시 호출되지 않아 잠금이 고착된다.
// - 권한: TRADE·ADMIN만 폼·버튼을 보인다. 서버 403이 오면 폼도 숨긴다(역할은 서버가 최종).
// - 확정 SO 경고(CONFIRMED_SO_ADVANCE_UNMET): 역기록은 막히지 않고 SO는 자동 취소되지 않는다 — 사람이 후속 조치.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../lib/api";
import { todayKst, toKstDisplay } from "../lib/datetime";
import { PAYMENT_TYPE_LABEL, proformaStatusLabel } from "../lib/doc-status";
import { DOCUMENT_FLOW_QUERY_KEY } from "../lib/sales-order";
import {
  PI_PAYMENT_OPEN_STATUSES,
  isForbidden,
  isRecoverableConflict,
  paymentErrorMessage,
  piPaymentsKey,
  validateReceipt,
  type PaymentPageData,
  type PaymentRow,
  type PaymentWriteResult,
} from "../lib/payment";
import { usePagedList } from "../lib/paging";
import { PROFORMAS_QUERY_KEY } from "../lib/proforma";
import { hasRole, useSession } from "../lib/session";
import { ConfirmDialog } from "./confirm-dialog";
import { ListPager } from "./list-pager";
import { ListState } from "./list-state";

export interface PaymentPanelPi {
  id: number;
  currency: string;
  minor_units: number;
  status: string;
  payment_type: string | null;
}

interface ReceiptBody {
  received_amount: string;
  received_currency: string;
  received_on: string;
  reference: string;
}

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";
const NOT_ADVANCE_NOTICE = "선수금 입금은 선수금 T/T 전표만 기록합니다(잔금 입금은 후속 기능).";

/** 같은 본문 → 같은 키. 본문이 달라지면 새 키(다른 요청이므로). */
function keyFor(map: Map<string, string>, serialized: string): string {
  const found = map.get(serialized);
  if (found !== undefined) return found;
  const created = crypto.randomUUID();
  map.set(serialized, created);
  return created;
}

export function PiPaymentsPanel({ pi, onWritten }: { pi: PaymentPanelPi; onWritten?: () => void }) {
  const { me } = useSession();
  const client = useQueryClient();
  const [forbidden, setForbidden] = useState(false);
  const canWrite = hasRole(me, "TRADE") && !forbidden;

  const listPath = `/v1/proforma-invoices/${pi.id}/payments`;
  const list = usePagedList<PaymentRow, PaymentPageData>(piPaymentsKey(pi.id), listPath, true, { staleTime: 0 });
  const summary = list.data?.summary;

  // 폼 상태
  const [amount, setAmount] = useState("");
  const [receivedOn, setReceivedOn] = useState(() => todayKst());
  const [reference, setReference] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [confirmBody, setConfirmBody] = useState<ReceiptBody | null>(null);
  const [reversing, setReversing] = useState<PaymentRow | null>(null);
  const [warningSos, setWarningSos] = useState<number[] | null>(null);

  const receiptKeys = useRef(new Map<string, string>());
  const reversalKeys = useRef(new Map<string, string>());
  const receiptLock = useRef(false);
  const reversalLock = useRef(false);
  const onWrittenRef = useRef(onWritten);
  onWrittenRef.current = onWritten;

  function afterWrite() {
    void client.invalidateQueries({ queryKey: piPaymentsKey(pi.id) });
    // PI 상세·목록·상태 이력(prefix)과 문서 흐름 — 입금은 PI 상태를 수렴시킨다.
    void client.invalidateQueries({ queryKey: PROFORMAS_QUERY_KEY });
    void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
    onWrittenRef.current?.();
  }

  const receipt = useMutation({
    // ★ 잠금 해제는 finally — 요청 중 reset()·언마운트에도 풀린다(mutate onSettled는 그때 호출되지 않는다).
    mutationFn: async (input: { body: ReceiptBody; key: string }) => {
      try {
        return await apiFetch<PaymentWriteResult>(listPath, {
          method: "POST",
          idempotencyKey: input.key,
          body: input.body,
        });
      } finally {
        receiptLock.current = false;
      }
    },
    onSuccess: (result) => {
      setConfirmBody(null);
      setAmount("");
      setReference("");
      setProblems([]);
      receiptKeys.current.clear();
      setWarningSos(firstWarning(result));
      afterWrite();
    },
    onError: (error) => {
      if (isForbidden(error)) {
        setForbidden(true);
        setConfirmBody(null);
      }
    },
  });

  const reversal = useMutation({
    mutationFn: async (input: { paymentId: number; reason: string; key: string }) => {
      try {
        return await apiFetch<PaymentWriteResult>(`/v1/payments/${input.paymentId}/reversal`, {
          method: "POST",
          idempotencyKey: input.key,
          body: { reason: input.reason },
        });
      } finally {
        reversalLock.current = false;
      }
    },
    onSuccess: (result) => {
      setReversing(null);
      reversalKeys.current.clear();
      setWarningSos(firstWarning(result));
      afterWrite();
    },
    onError: (error) => {
      if (isForbidden(error)) {
        setForbidden(true);
        setReversing(null);
      }
    },
  });

  function submitReceipt(body: ReceiptBody) {
    if (receiptLock.current) return;
    receiptLock.current = true;
    receipt.mutate({ body, key: keyFor(receiptKeys.current, JSON.stringify(body)) });
  }

  function submitReversal(payment: PaymentRow, reason: string) {
    if (reversalLock.current) return;
    reversalLock.current = true;
    reversal.mutate({
      paymentId: payment.id,
      reason,
      key: keyFor(reversalKeys.current, JSON.stringify([payment.id, reason])),
    });
  }

  /** 충돌(409) 안내의 '최신 내용 불러오기' — 다이얼로그를 닫고 입금 목록·PI를 다시 읽는다. */
  function reload() {
    receipt.reset();
    reversal.reset();
    setConfirmBody(null);
    setReversing(null);
    afterWrite();
  }

  // PI·목록이 바뀌어 더는 입금 가능한 상태가 아니면 열린 확인창을 닫는다(옛 화면으로 쓰지 않게).
  const piOpen = PI_PAYMENT_OPEN_STATUSES.includes(summary?.pi_status ?? pi.status);
  useEffect(() => {
    if (!piOpen) setConfirmBody(null);
  }, [piOpen]);

  const paymentType = summary?.payment_type ?? pi.payment_type;
  const isAdvance = paymentType === "TT_ADVANCE";
  const fullyPaid = summary !== undefined && summary.remaining_amount === 0;

  function onSubmitForm(event: React.FormEvent) {
    event.preventDefault();
    const found = validateReceipt({ amount, receivedOn, reference }, pi.minor_units, todayKst());
    setProblems(found);
    if (found.length > 0) return;
    receipt.reset();
    setConfirmBody({
      received_amount: amount.trim(),
      received_currency: pi.currency,
      received_on: receivedOn,
      reference: reference.trim(),
    });
  }

  const formBlockedReason = !piOpen
    ? "취소되었거나 만료된 PI에는 입금을 기록할 수 없습니다."
    : fullyPaid
      ? "선수금이 모두 입금되었습니다. 정정이 필요하면 아래 입금 내역에서 역기록한 뒤 다시 입금하세요."
      : null;

  return (
    <section aria-labelledby="pi-payments-title" className="rounded-lg border border-gray-200 p-4">
      <h2 id="pi-payments-title" className="text-lg font-semibold">
        입금
      </h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        바이어가 보낸 선수금을 은행 확인 후 기록합니다. 기록은 수정·삭제할 수 없고, 잘못 기록했으면 역기록한 뒤 다시 입금하세요.
      </p>

      {summary !== undefined && (
        <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4" aria-label="입금 요약">
          <SummaryCell label="순입금" value={moneyText(summary.net_received_text, summary.currency)} />
          <SummaryCell label="선수금" value={moneyText(summary.due_text, summary.currency)} />
          <SummaryCell label="잔여" value={moneyText(summary.remaining_text, summary.currency)} />
          <SummaryCell label="PI 상태" value={proformaStatusLabel(summary.pi_status)} />
        </dl>
      )}

      {warningSos !== null && (
        <div role="alert" className="mt-4 rounded border border-signal-red p-3 text-sm text-signal-red">
          <p className="break-keep font-medium">확정된 수주가 있는 PI의 입금이 선수금 조건 미만이 되었습니다.</p>
          <p className="mt-1 break-keep">
            해당 수주는 자동 취소되지 않았습니다. 사람이 후속 조치(취소·재확인)를 하세요.
          </p>
          <ul className="mt-2 flex flex-wrap gap-3">
            {warningSos.map((soId) => (
              <li key={soId}>
                <Link to={`/sales-orders/${soId}`} className="cell-nowrap underline">
                  수주 #{soId}
                </Link>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setWarningSos(null)}
            className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1"
          >
            확인했습니다
          </button>
        </div>
      )}

      {!isAdvance && paymentType !== null && summary !== undefined && (
        <p role="note" className="mt-4 break-keep rounded border border-gray-300 bg-gray-50 p-3 text-sm text-gray-700">
          {NOT_ADVANCE_NOTICE} 이 PI의 결제유형: {PAYMENT_TYPE_LABEL[paymentType] ?? "확인 불가"}
        </p>
      )}
      {!isAdvance && paymentType === null && summary !== undefined && (
        <p role="note" className="mt-4 break-keep rounded border border-gray-300 bg-gray-50 p-3 text-sm text-gray-700">
          {NOT_ADVANCE_NOTICE}
        </p>
      )}

      {isAdvance && canWrite && (
        <form
          aria-label="입금 기록"
          onSubmit={onSubmitForm}
          noValidate
          className="mt-4 rounded border border-gray-200 p-3"
        >
          <h3 className="font-semibold">입금 기록</h3>
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">입금액</span>
              <span className="flex items-center gap-2">
                <input
                  value={amount}
                  inputMode="decimal"
                  autoComplete="off"
                  onChange={(event) => setAmount(event.target.value)}
                  className={`${inputClass} num w-full`}
                />
                <span className="cell-nowrap font-medium" aria-label="통화(PI 통화로 고정)">
                  {pi.currency}
                </span>
              </span>
              <span className="break-keep text-xs text-gray-500">통화는 PI 통화로 고정됩니다(환산 입금 불가).</span>
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">입금일 (KST)</span>
              <input
                type="date"
                value={receivedOn}
                max={todayKst()}
                onChange={(event) => setReceivedOn(event.target.value)}
                className={inputClass}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-gray-600">입금 확인 근거 (필수)</span>
              <input
                value={reference}
                maxLength={100}
                autoComplete="off"
                onChange={(event) => setReference(event.target.value)}
                className={inputClass}
              />
              <span className="break-keep text-xs text-gray-500">은행 거래 참조번호나 확인 메모를 적습니다.</span>
            </label>
          </div>
          {problems.length > 0 && (
            <ul role="alert" className="mt-3 list-disc break-keep pl-5 text-sm text-signal-red">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          <button
            type="submit"
            disabled={formBlockedReason !== null}
            title={formBlockedReason ?? undefined}
            aria-describedby={formBlockedReason ? "pi-payment-blocked" : undefined}
            className="cell-nowrap mt-3 rounded border border-gray-900 px-4 py-2 text-sm disabled:opacity-50"
          >
            입금 기록
          </button>
          {formBlockedReason && (
            <p id="pi-payment-blocked" className="mt-2 break-keep text-xs text-gray-500">
              {formBlockedReason}
            </p>
          )}
        </form>
      )}
      {isAdvance && !canWrite && (
        <p className="mt-4 break-keep text-sm text-gray-600">
          입금 기록·역기록은 무역·관리자만 할 수 있습니다. 내역은 아래에서 확인할 수 있습니다.
        </p>
      )}

      <h3 className="mt-6 font-semibold">입금 내역</h3>
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data !== undefined && list.data.items.length === 0}
          emptyHint="기록된 입금이 없습니다."
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-3 py-2 text-center">구분</th>
                <th className="cell-nowrap px-3 py-2 text-center">금액</th>
                <th className="cell-nowrap px-3 py-2 text-center">입금일</th>
                <th className="cell-nowrap px-3 py-2">입금 확인 근거·사유</th>
                <th className="cell-nowrap px-3 py-2 text-center">기록일시</th>
                {canWrite && <th className="cell-nowrap px-3 py-2 text-center">처리</th>}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <PaymentRowView
                  key={row.id}
                  row={row}
                  canWrite={canWrite}
                  piOpen={piOpen}
                  onReverse={() => {
                    reversal.reset();
                    setReversing(row);
                  }}
                />
              ))}
            </tbody>
          </table>
        </ListState>
      </div>
      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-2" />

      {confirmBody !== null && (
        <ConfirmDialog
          title="입금을 기록할까요?"
          danger
          confirmLabel="입금 기록 확정"
          description={
            <div className="grid gap-2">
              <p>
                <strong className="num">
                  {confirmBody.received_amount} {confirmBody.received_currency}
                </strong>
                을(를) <span className="cell-nowrap">{confirmBody.received_on}</span>자 입금으로 기록합니다.
              </p>
              {summary?.remaining_text != null && (
                <p>
                  남은 선수금: <span className="num">{summary.remaining_text}</span> {summary.currency}. 이 금액을 넘으면
                  기록되지 않습니다.
                </p>
              )}
              <p>
                기록하면 <strong>되돌릴 수 없습니다.</strong> 잘못 기록했으면 수정할 수 없고, 역기록한 뒤 다시 입금해야 합니다.
                입금이 반영되면 PI 상태가 자동으로 바뀝니다.
              </p>
            </div>
          }
          pending={receipt.isPending}
          error={receipt.error ? paymentErrorMessage(receipt.error) : null}
          onReload={isRecoverableConflict(receipt.error) ? reload : undefined}
          onCancel={() => {
            // 요청 중 닫기(Esc)는 무시 — 닫고 reset하면 응답을 못 본 채 같은 키로 재시도하게 된다.
            if (receipt.isPending) return;
            receipt.reset();
            setConfirmBody(null);
          }}
          onConfirm={() => submitReceipt(confirmBody)}
        />
      )}
      {reversing !== null && (
        <ConfirmDialog
          title="입금을 역기록할까요?"
          danger
          confirmLabel="역기록 확정"
          reasonLabel="역기록 사유 (필수, 2자 이상)"
          reasonMinLength={2}
          reasonMaxLength={300}
          description={
            <div className="grid gap-2">
              <p>
                입금 <strong className="num">{reversing.received_amount_text} {reversing.received_currency}</strong> (
                <span className="cell-nowrap">{reversing.received_on}</span>)을(를) 전액 역기록합니다.
              </p>
              <p>
                역기록하면 <strong>되돌릴 수 없습니다.</strong> 원 기록은 그대로 남고 반대 금액이 새로 기록되며, PI 상태가
                자동으로 바뀝니다. 정정이 필요하면 역기록 후 새로 입금하세요.
              </p>
            </div>
          }
          pending={reversal.isPending}
          error={reversal.error ? paymentErrorMessage(reversal.error) : null}
          onReload={isRecoverableConflict(reversal.error) ? reload : undefined}
          onCancel={() => {
            if (reversal.isPending) return;
            reversal.reset();
            setReversing(null);
          }}
          onConfirm={(reason) => submitReversal(reversing, reason)}
        />
      )}
    </section>
  );
}

function firstWarning(result: PaymentWriteResult): number[] | null {
  const found = result.warnings.find((warning) => warning.code === "CONFIRMED_SO_ADVANCE_UNMET");
  return found ? found.sales_order_ids : null;
}

function moneyText(text: string | null, currency: string): string {
  return text === null ? "-" : `${text} ${currency}`;
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <dt className="cell-nowrap text-xs text-gray-500">{label}</dt>
      <dd className="num cell-nowrap mt-1 text-base font-semibold">{value}</dd>
    </div>
  );
}

function PaymentRowView({
  row,
  canWrite,
  piOpen,
  onReverse,
}: {
  row: PaymentRow;
  canWrite: boolean;
  piOpen: boolean;
  onReverse: () => void;
}) {
  const isReversal = row.kind === "REVERSAL";
  const reversed = row.reversed_by_payment_id !== null;
  const canReverse = canWrite && !isReversal && !reversed;
  return (
    <tr className={`border-t border-gray-100 align-top ${reversed ? "text-gray-500" : ""}`}>
      <td className="cell-nowrap px-3 py-2 text-center">
        <span
          className={`rounded border px-2 py-0.5 text-xs ${
            isReversal ? "border-signal-red text-signal-red" : "border-gray-300"
          }`}
        >
          {isReversal ? "역기록" : "입금"}
        </span>
        {reversed && (
          <span className="mt-1 block text-xs text-gray-500">역기록됨 (#{row.reversed_by_payment_id})</span>
        )}
        {isReversal && row.reverses_payment_id !== null && (
          <span className="mt-1 block text-xs text-gray-500">원 입금 #{row.reverses_payment_id}</span>
        )}
      </td>
      <td className={`num cell-nowrap px-3 py-2 ${reversed ? "line-through" : ""}`}>
        {row.received_amount_text} {row.received_currency}
      </td>
      <td className="num cell-nowrap px-3 py-2">{row.received_on}</td>
      <td className="break-keep px-3 py-2">
        {row.reference}
        {row.reason && <span className="block text-xs text-gray-500">사유: {row.reason}</span>}
      </td>
      <td className="num cell-nowrap px-3 py-2">{toKstDisplay(row.created_at)}</td>
      {canWrite && (
        <td className="cell-nowrap px-3 py-2 text-center">
          {canReverse && (
            <button
              type="button"
              onClick={onReverse}
              disabled={!piOpen}
              title={piOpen ? undefined : "취소되었거나 만료된 PI의 입금은 역기록할 수 없습니다."}
              aria-label={`입금 #${row.id} 역기록`}
              className="cell-nowrap rounded border border-signal-red px-3 py-1 text-sm text-signal-red disabled:opacity-50"
            >
              역기록
            </button>
          )}
        </td>
      )}
    </tr>
  );
}
