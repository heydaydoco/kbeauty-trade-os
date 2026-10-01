// 여신 평가 카드 (S3-1 PR-12b — design-D D5 / ADR-0070). 서버가 준 CREDIT 게이트 basis를 그대로 보인다.
//
// 규칙:
// - ★ 한도 초과 여부·승인 필요 여부는 프런트가 판정하지 않는다: basis.verdict(서버)의 한국어 라벨과 서버 message_ko만 표시한다.
// - ★ 금액은 서버 표시용 문자열(`*_text`)이 있을 때만 보인다. basis는 정수 최소단위뿐이라(11b 부채) 없으면 표시하지 않고 승인 상세로 안내한다 — 프런트 산술 0.
// - 마스킹 역할(서버가 basis를 {}·basis_hash를 ""로 줌)에게는 수치 비공개 안내만.
// - '미수 미반영' 경고는 9b approval-snapshot과 같은 규칙(receivables_reflected가 true가 아니면 — 키가 없어도 — 경고)이다.

import { Link } from "react-router";
import { reasonCodeText, snapshotFacts, verdictLabel } from "../lib/approval";
import { levelBadgeClass, levelLabel } from "../lib/gate";

export interface CreditRowView {
  level: string;
  message_ko: string;
  basis: Record<string, unknown>;
  basis_hash: string;
}

const TEXT_FIELDS: ReadonlyArray<[key: string, label: string]> = [
  ["limit_amount", "여신 한도"],
  ["open_orders_amount", "진행 중 주문 합계"],
  ["this_order_amount", "이번 수주"],
  ["exposure_after_amount", "반영 후 노출"],
  ["excess_amount", "초과분"],
];

const textOf = (basis: Record<string, unknown>, key: string): string | null => {
  const value = basis[`${key}_text`];
  return typeof value === "string" && value !== "" ? value : null;
};

export function CreditEvaluationCard({
  row,
  source,
  approvalId,
}: {
  row: CreditRowView;
  /** blocked = 확정 시도(서버 잠금 하 평가) 시점의 값 / report = 방금 조회한 참고 판정. */
  source: "blocked" | "report";
  /** 승인 상세로 안내할 승인 id(있을 때만). */
  approvalId: number | null;
}) {
  // 마스킹 역할은 basis_hash가 빈 문자열이다(서버 규칙) — 값이 없는 것과 마스킹을 구분한다.
  const masked = row.basis_hash === "";
  const facts = snapshotFacts(row.basis);
  const shown = TEXT_FIELDS.flatMap(([key, label]) => {
    const text = textOf(row.basis, key);
    return text === null ? [] : [{ key, label, text }];
  });

  return (
    <section aria-labelledby="credit-card-title" className="rounded border border-gray-200 p-3">
      <h3 id="credit-card-title" className="font-semibold">
        여신 평가
      </h3>
      <p className="mt-1 break-keep text-xs text-gray-500">
        {source === "blocked"
          ? "확정을 시도한 시점에 서버가 평가한 값입니다."
          : "방금 조회한 참고 평가입니다. 확정 시 서버가 다시 평가합니다."}
        {row.basis.advisory === true && " (잠금 없는 참고 평가)"}
      </p>

      <p className="mt-2 break-keep text-sm">
        <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${levelBadgeClass(row.level)}`}>{levelLabel(row.level)}</span>{" "}
        {row.message_ko}
      </p>

      {masked ? (
        <p className="mt-2 break-keep text-sm text-gray-600">여신 수치(한도·노출)는 무역·관리자에게만 표시됩니다.</p>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap gap-2">
            {!facts.receivablesReflected && (
              <span role="status" className="cell-nowrap rounded border border-signal-red px-2 py-0.5 text-xs font-medium text-signal-red">
                미수 미반영
              </span>
            )}
            {facts.exposureIsPartial && (
              <span role="status" className="cell-nowrap rounded border border-gray-500 bg-gray-200 px-2 py-0.5 text-xs text-gray-800">
                노출 일부만 반영
              </span>
            )}
            {facts.verdict === "UNEVALUABLE" && (
              <span role="status" className="cell-nowrap rounded border border-signal-red px-2 py-0.5 text-xs font-medium text-signal-red">
                평가 불능
              </span>
            )}
          </div>
          {!facts.receivablesReflected && (
            <p className="mt-2 break-keep text-sm text-signal-red">
              이 노출에는 미수금이 반영되지 않았습니다. 실제 노출은 평가값보다 클 수 있으니 미수 현황을 따로 확인하세요.
            </p>
          )}
          {facts.exposureIsPartial && (
            <p className="mt-2 break-keep text-sm text-gray-700">환산하지 못한 주문이 있어 노출 일부만 계산되었습니다.</p>
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
          <dl className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <dt className="text-xs text-gray-500">평가 결과(서버)</dt>
              <dd className="break-keep text-center">{facts.verdict === null ? "—" : verdictLabel(facts.verdict)}</dd>
            </div>
            {shown.map((item) => (
              <div key={item.key}>
                <dt className="text-xs text-gray-500">{item.label}</dt>
                <dd className="num text-center">{item.text}</dd>
              </div>
            ))}
          </dl>
          {shown.length === 0 && (
            <p className="mt-2 break-keep text-sm text-gray-600">
              금액은 이 화면에 표시하지 않습니다(서버가 표시용 금액을 주지 않음).
              {approvalId !== null ? (
                <>
                  {" "}
                  정확한 수치는{" "}
                  <Link to={`/approvals/${approvalId}`} className="cell-nowrap underline">
                    승인 #{approvalId} 상세
                  </Link>
                  의 근거에서 확인하세요.
                </>
              ) : (
                " 승인을 요청하면 승인 상세에서 요청 시점의 수치를 확인할 수 있습니다."
              )}
            </p>
          )}
        </>
      )}
    </section>
  );
}
