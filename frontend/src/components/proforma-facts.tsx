// PI 상세·미리보기 공용 표시 조각 — 결제조건·Incoterms·은행 스냅샷·선수금 청구액 (S3-1 PR-6b).
// 값은 전부 서버가 준 문자열·코드 그대로다(금액 산술 0). 미리보기와 상세가 같은 모양이어야 "본 대로 만들어진다".

import type { ReactNode } from "react";
import { BALANCE_ANCHOR_LABEL, PAYMENT_TYPE_LABEL } from "../lib/doc-status";
import type { Advance, BankSnapshot, Incoterm, PaymentTerms } from "../lib/proforma";

export const EMPTY = "—";
export const show = (value: string | number | null | undefined) =>
  value === null || value === undefined || value === "" ? EMPTY : String(value);

export function DocField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="cell-nowrap text-xs text-gray-500">{label}</dt>
      <dd className="mt-0.5 break-keep text-sm">{children}</dd>
    </div>
  );
}

export function paymentTermsText(terms: PaymentTerms): string {
  if (terms.payment_type === null) return EMPTY;
  const parts: string[] = [PAYMENT_TYPE_LABEL[terms.payment_type] ?? terms.payment_type];
  if (terms.advance_pct !== null) parts.push(`선수금 ${terms.advance_pct}%`);
  if (terms.balance_anchor !== null) {
    parts.push(
      `잔금 ${BALANCE_ANCHOR_LABEL[terms.balance_anchor] ?? terms.balance_anchor} 기준 ${show(terms.balance_days)}일`,
    );
  }
  return parts.join(" · ");
}

export function incotermText(incoterm: Incoterm): string {
  return incoterm.code === null
    ? EMPTY
    : `${incoterm.code} ${show(incoterm.place)} (${show(incoterm.year)})`;
}

/** 발행 시점 은행정보 스냅샷 — 계좌 마스터를 나중에 고쳐도 이 값은 바뀌지 않는다. */
export function BankSnapshotView({ bank }: { bank: BankSnapshot }) {
  return (
    <section aria-label="입금 은행 정보" className="rounded-lg border border-gray-200 p-4">
      <h2 className="text-lg font-semibold">입금 은행 정보</h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        발행 시점의 값이 복사되어 있습니다. 은행 계좌를 나중에 고쳐도 이 청구서는 바뀌지 않습니다.
      </p>
      <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <DocField label="수취인">{bank.beneficiary_name}</DocField>
        <DocField label="수취인 주소">{bank.beneficiary_address}</DocField>
        <DocField label="은행">{bank.bank_name}</DocField>
        <DocField label="은행 주소">{bank.bank_address}</DocField>
        <DocField label="계좌번호">
          <span className="cell-nowrap num">{bank.account_no}</span>
        </DocField>
        <DocField label="SWIFT">
          <span className="cell-nowrap">{bank.swift_code}</span>
        </DocField>
      </dl>
    </section>
  );
}

/** 선수금 청구액 — 서버가 계산한 문자열(HALF_UP). 선수금 T/T가 아니면 null이라 아무것도 그리지 않는다. */
export function AdvanceView({ advance, currency }: { advance: Advance | null; currency: string }) {
  if (advance === null) return null;
  return (
    <dl aria-label="선수금 청구액" className="grid grid-cols-2 gap-4 text-sm">
      <DocField label="선수금 청구액 (서버 계산)">
        <span className="num cell-nowrap font-semibold">
          {advance.advance_text} {currency}
        </span>
      </DocField>
      <DocField label="잔금 (서버 계산)">
        <span className="num cell-nowrap">
          {advance.balance_text} {currency}
        </span>
      </DocField>
    </dl>
  );
}
