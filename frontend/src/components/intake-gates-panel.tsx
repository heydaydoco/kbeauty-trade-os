// 인테이크 게이트 요약 (S3-1 PR-13b — GET /order-intakes/{id}/gates). 서버가 계산한 검토 시점 **정보**를 그대로 보인다.
//
// ★ 프런트는 통과·확정 가능 여부를 다시 판정하지 않는다: `intake_confirmable`·`gates[].level`·`blocks_intake_confirm`·`settlement`·`message_ko`는 서버 값 그대로.
//   `intake_confirmable`은 하드 게이트 2종(품번 매핑·중복 PO)의 사전 점검일 뿐 확정 가능 보장이 아니다 — 입력 완결성·확정 시점 납기 경과·복제 원본 자격은 확정 때 서버가 다시 검사한다.
// 점유 문서번호·상태(`detail`)는 서버가 줄 때(무역·관리자)만 보인다 — 마스킹 역할은 detail이 비어 있다.

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import { gateLabel, levelBadgeClass, levelLabel, settlementLabel } from "../lib/gate";
import { intakeLoadErrorMessage, intakeStatusLabel, orderIntakeGatesKey, type IntakeGateReport, type IntakeGateResult } from "../lib/order-intake";
import { salesOrderStatusLabel } from "../lib/doc-status";

const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);

function occupantOf(row: IntakeGateResult): string | null {
  const doc = str(row.detail?.other_doc_number);
  const status = str(row.detail?.other_status);
  const intakeId = typeof row.detail?.other_intake_id === "number" ? row.detail.other_intake_id : null;
  if (doc !== null) return `점유 문서: ${doc}${status === null ? "" : ` (${salesOrderStatusLabel(status)})`}`;
  if (intakeId !== null) return `점유 문서: 대기 인테이크 #${intakeId}${status === null ? "" : ` (${intakeStatusLabel(status)})`}`;
  return null;
}

export function IntakeGatesPanel({ intakeId, enabled }: { intakeId: number; enabled: boolean }) {
  const report = useQuery({
    queryKey: orderIntakeGatesKey(intakeId),
    queryFn: () => apiFetch<IntakeGateReport>(`/v1/order-intakes/${intakeId}/gates`),
    enabled: enabled && Number.isInteger(intakeId) && intakeId > 0,
    // 참고값 — 품번 매핑을 고치고 돌아왔을 때 옛 판정이 남지 않게.
    staleTime: 0,
  });
  const data = report.data;
  const gates = Array.isArray(data?.gates) ? data.gates : null;

  return (
    <section aria-labelledby="intake-gates-title" className="rounded-lg border border-gray-200 p-4">
      <h2 id="intake-gates-title" className="text-lg font-semibold">
        접수 게이트 요약
      </h2>
      {!enabled ? (
        <p role="note" className="mt-2 break-keep text-sm text-gray-600">
          게이트 요약은 대기 상태의 인테이크에서만 계산합니다.
        </p>
      ) : report.isPending ? (
        <p role="status" className="mt-2 text-sm text-gray-500">
          게이트 판정을 불러오는 중…
        </p>
      ) : report.isError || data === undefined || gates === null ? (
        <div role="alert" className="mt-2 text-sm text-signal-red">
          <p className="break-keep">{intakeLoadErrorMessage(report.error, "게이트 판정")} 그래도 확정은 서버가 다시 판정합니다.</p>
          <button type="button" onClick={() => void report.refetch()} className="cell-nowrap mt-2 rounded border border-gray-300 px-3 py-1 text-gray-900">
            다시 시도
          </button>
        </div>
      ) : (
        <div className="mt-2 grid gap-3">
          <p className="break-keep text-sm text-gray-700">
            서버 사전 점검(하드 게이트 2종 — 품번 매핑·중복 PO):{" "}
            <strong>{data.intake_confirmable ? "통과" : "통과하지 못함"}</strong>. 이것은 확정 가능 보장이 아닙니다 — 입력 완결성·확정 시점 요청납기·복제 원본 자격은 확정할 때
            서버가 다시 검사하며, 최종 판정은 확정 시 서버가 합니다.
          </p>
          <p className="break-keep text-xs text-gray-500">
            방금 조회한 참고 판정입니다({toKstDisplay(data.evaluated_at)}). 가격·MOQ·시장 준비도는 접수 확정을 막지 않고 수주 확정 단계에서 판정합니다.
          </p>
          {gates.length === 0 ? (
            <p className="text-sm text-gray-500">평가 결과가 없습니다.</p>
          ) : (
            <div className="overflow-x-auto rounded border border-gray-200">
              <table className="w-full text-sm">
                <caption className="sr-only">인테이크 게이트 판정</caption>
                <thead className="bg-gray-50 text-left text-gray-600">
                  <tr>
                    <th scope="col" className="cell-nowrap px-3 py-2">게이트</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">결과</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">접수 확정</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">정산</th>
                    <th scope="col" className="px-3 py-2">설명</th>
                  </tr>
                </thead>
                <tbody>
                  {gates.map((row, index) => {
                    const occupant = occupantOf(row);
                    return (
                      <tr key={`${row.gate_code}-${row.line_id ?? "h"}-${index}`} className="border-t border-gray-100 align-top">
                        <td className="cell-nowrap px-3 py-2">
                          <span className="font-medium">{gateLabel(row.gate_code)}</span>
                          {row.line_id !== null && <span className="block text-xs text-gray-500">라인 {row.line_no ?? "?"}</span>}
                        </td>
                        <td className="px-3 py-2 text-center">
                          <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${levelBadgeClass(row.level)}`}>{levelLabel(row.level)}</span>
                        </td>
                        <td className="cell-nowrap px-3 py-2 text-center text-xs">{row.blocks_intake_confirm ? "확정을 막는 항목" : "참고"}</td>
                        <td className="cell-nowrap px-3 py-2 text-center text-xs">{settlementLabel(row.settlement)}</td>
                        <td className="break-keep px-3 py-2">
                          {row.message_ko}
                          {occupant !== null && <span className="mt-1 block text-xs text-gray-600">{occupant}</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {data.readiness_scope_note && <p className="break-keep text-xs text-gray-500">{data.readiness_scope_note}</p>}
          {data.note && <p className="break-keep text-xs text-gray-500">{data.note}</p>}
        </div>
      )}
    </section>
  );
}
