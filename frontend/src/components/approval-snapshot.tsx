// 승인 요청 스냅샷 근거 (S3-1 PR-9b — design-C C1·C8 / 승인 통제 규칙 ④).
//
// 요청 시점에 동결된 여신 평가 값을 서버 값 그대로(자릿수만 옮겨) 보인다 — 프런트 산술 0.
// ★ 미수 미반영(provider 미등록·조회 실패)이면 경고 badge를 항상 띄운다: 노출이 실제보다 작게 보일 수 있다.

import { useCurrencies } from "../lib/money";
import {
  moneyText,
  reasonCodeText,
  snapshotFacts,
  verdictLabel,
  type ApprovalView,
} from "../lib/approval";
import { DocField, show } from "./proforma-facts";

export function ApprovalSnapshot({ approval }: { approval: ApprovalView }) {
  const currencies = useCurrencies();
  const list = currencies.data?.items;
  const facts = snapshotFacts(approval.snapshot);
  // 여신 비관리(한도 없음)는 미수 경고 대상이 아니다 — 한도 없음 안내만(빨간 미수 경고와 병기하지 않는다).
  const notManaged = facts.verdict === "NOT_MANAGED";
  const cur = facts.limitCurrency ?? approval.basis_currency;
  const money = (amount: number | null) => moneyText(amount, cur, list);

  return (
    <section aria-labelledby="approval-snapshot-title" className="rounded-lg border border-gray-200 p-4">
      <h2 id="approval-snapshot-title" className="text-lg font-semibold">
        승인 근거 (요청 시점 스냅샷)
      </h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        요청한 순간에 동결된 값입니다. 이후 거래처 한도나 주문이 바뀌어도 이 값은 변하지 않습니다.
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {!notManaged && !facts.receivablesReflected && (
          <span
            role="status"
            className="cell-nowrap rounded border border-signal-red px-2 py-0.5 text-xs font-medium text-signal-red"
          >
            미수 미반영
          </span>
        )}
        {!notManaged && facts.exposureIsPartial && (
          <span
            role="status"
            className="cell-nowrap rounded border border-gray-500 bg-gray-200 px-2 py-0.5 text-xs text-gray-800"
          >
            노출 일부만 반영
          </span>
        )}
        {facts.verdict === "UNEVALUABLE" && (
          <span
            role="status"
            className="cell-nowrap rounded border border-signal-red px-2 py-0.5 text-xs font-medium text-signal-red"
          >
            평가 불능
          </span>
        )}
      </div>
      {!notManaged && !facts.receivablesReflected && (
        <p className="mt-2 break-keep text-sm text-signal-red">
          이 노출에는 미수금이 반영되지 않았습니다. 실제 노출은 아래 금액보다 클 수 있으니 미수 현황을 따로 확인한 뒤 결정해
          주세요.
        </p>
      )}
      {!notManaged && facts.exposureIsPartial && (
        <p className="mt-2 break-keep text-sm text-gray-700">
          미수 채권이 아직 노출에 반영되지 않아 실제 노출은 더 클 수 있습니다.
        </p>
      )}
      {notManaged && (
        <p className="mt-2 break-keep text-sm text-gray-700">여신 한도가 없는(여신 비관리) 거래처라 한도·미수 확인 대상이 아닙니다.</p>
      )}
      {facts.verdict === "UNEVALUABLE" && (
        <p className="mt-2 break-keep text-sm text-signal-red">
          여신을 평가하지 못한 상태입니다. 한도 초과 여부를 이 화면의 값만으로 판단할 수 없습니다.
        </p>
      )}
      {facts.reasonCodes.length > 0 && (
        <ul className="mt-2 list-disc pl-5 text-sm text-gray-700">
          {facts.reasonCodes.map((code) => (
            <li key={code} className="break-keep">
              {reasonCodeText(code)}
            </li>
          ))}
        </ul>
      )}

      <dl className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <DocField label="평가 결과">{facts.verdict === null ? "—" : verdictLabel(facts.verdict)}</DocField>
        <DocField label="여신 한도">
          <span className="num block text-center">{money(facts.limit)}</span>
        </DocField>
        <DocField label="진행 중 주문 합계">
          <span className="num block text-center">{money(facts.openOrders)}</span>
        </DocField>
        <DocField label="이번 수주">
          <span className="num block text-center">{money(facts.thisOrder)}</span>
        </DocField>
        <DocField label="반영 후 노출">
          <span className="num block text-center">{money(facts.exposureAfter)}</span>
        </DocField>
        <DocField label="초과분">
          <span className="num block text-center font-semibold">{money(facts.excess)}</span>
        </DocField>
        <DocField label="미수금">
          {facts.receivablesReflected ? "반영됨" : "미반영"}
        </DocField>
        <DocField label="승인 기준 금액">
          <span className="num block text-center">{moneyText(approval.basis_amount, approval.basis_currency, list)}</span>
        </DocField>
        {facts.fxRate !== null && (
          <DocField label="환산 증빙(전표 환율)">
            {show(facts.fxDocCurrency)} 환율 {facts.fxRate} ({show(facts.fxRateDate)})
          </DocField>
        )}
      </dl>
    </section>
  );
}
