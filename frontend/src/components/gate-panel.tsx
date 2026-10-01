// 게이트 패널 (S3-1 PR-11b — design-D D3·D7 / ADR-0069). SO 상세에서 7종 게이트의 '현재 판정'을 보이고 예외 승인(override)을 부여·철회한다.
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - ★ 프런트는 통과·미통과를 다시 판정하지 않는다: level·resolution·settlement·can_override·clearance·approval은 서버 값을 그대로 표시한다.
// - GET 결과는 READ COMMITTED 참고값(확정 증거 아님) — 확정 시 서버가 다시 평가한다. 확정 버튼은 PR-12 몫이라 없다.
// - 역할 마스킹: 여신 수치·basis_hash·override 사유는 TRADE·ADMIN에게만 온다(그 외는 null/빈 값) — 타입을 nullable로 받아 부재를 그대로 보인다.
// - 멱등 키: 본문(JSON) 기준 Map으로 재사용(같은 본문 재시도=같은 키), 성공하면 Map을 비운다. 더블클릭 잠금(ref)은 mutationFn의 finally에서 푼다.
// - 다이얼로그는 열 때의 판정 스냅샷을 들고 있다 — 창 포커스 재조회로 목록이 바뀌어도 열린 다이얼로그·입력은 닫히거나 지워지지 않는다(서버가 STALE로 막는다).
// - 쓰기 성공 뒤 게이트·SO 상세를 무효화한다. SO version 기준(baseVersion)은 건드리지 않는다(override는 SO 행을 바꾸지 않는다).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import { salesOrderStatusLabel } from "../lib/doc-status";
import {
  POLICY_UNSET,
  PI_MODE_KEY,
  PRICE_TOLERANCE_KEY,
  gateErrorMessage,
  gateLoadErrorMessage,
  gateLabel,
  gateTargetLabel,
  gatesKey,
  isGateRecoverable,
  isRouteForbidden,
  levelBadgeClass,
  levelLabel,
  newGateKey,
  piModeLabel,
  resolutionLabel,
  salesOrderDetailKey,
  settlementLabel,
  toleranceLabel,
  validateOverrideReason,
  type GateOverrideBody,
  type GateReport,
  type GateResult,
} from "../lib/gate";
import { DOCUMENT_FLOW_QUERY_KEY } from "../lib/sales-order";
import { hasRole, useSession } from "../lib/session";
import { ConfirmDialog } from "./confirm-dialog";

type Mode = "grant" | "revoke";
interface DialogState {
  mode: Mode;
  row: GateResult;
}

const ROLE_LABEL: Record<string, string> = { TRADE: "무역", ADMIN: "관리자" };
const roleLabel = (role: string): string => ROLE_LABEL[role] ?? "기타 역할";
const OPEN_STATUS = "RECEIVED";

/** 같은 본문 → 같은 키. 본문이 달라지면 새 키(다른 요청이므로). */
function keyFor(map: Map<string, string>, serialized: string): string {
  const found = map.get(serialized);
  if (found !== undefined) return found;
  const created = newGateKey();
  map.set(serialized, created);
  return created;
}

const rowKey = (row: Pick<GateResult, "gate_code" | "line_id" | "basis_hash">): string =>
  `${row.gate_code}:${row.line_id ?? "-"}:${row.basis_hash}`;

export function GatePanel({ soId, soStatus }: { soId: number; soStatus: string }) {
  const { me } = useSession();
  const client = useQueryClient();
  const [forbidden, setForbidden] = useState(false);
  const roleWrite = hasRole(me, "TRADE") && !forbidden;
  const isAdmin = me?.roles.includes("ADMIN") ?? false;

  const report = useQuery({
    queryKey: gatesKey(soId),
    queryFn: () => apiFetch<GateReport>(`/v1/sales-orders/${soId}/gates`),
    enabled: Number.isInteger(soId) && soId > 0,
    // 참고값 — 다른 화면에서 고치고 돌아왔을 때 옛 판정이 남지 않게. 창 포커스 재조회는 다이얼로그 상태와 무관하다(상태는 컴포넌트에 있다).
    staleTime: 0,
  });

  const [dialog, setDialog] = useState<DialogState | null>(null);
  // 성공 알림(live region)과 포커스 이동 — 다이얼로그가 닫히고 버튼이 사라져도 포커스가 body로 빠지지 않게 요약 영역으로 옮긴다.
  const [done, setDone] = useState<{ text: string; n: number } | null>(null);
  const summaryRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (done !== null) summaryRef.current?.focus();
  }, [done]);
  const grantKeys = useRef(new Map<string, string>());
  const revokeKeys = useRef(new Map<string, string>());
  const lock = useRef(false);

  function refresh() {
    void client.invalidateQueries({ queryKey: gatesKey(soId) });
    void client.invalidateQueries({ queryKey: salesOrderDetailKey(soId) });
  }

  const write = useMutation({
    // 오프라인이어도 요청을 보내 본다 — 기본(online)은 요청이 paused로 멈춰 다이얼로그가 갇힌다.
    networkMode: "always",
    // ★ 잠금 해제는 finally — 요청 중 reset()·언마운트에도 풀린다.
    mutationFn: async (input: { mode: Mode; body: GateOverrideBody; key: string }) => {
      try {
        const path = input.mode === "grant" ? "gate-overrides" : "gate-overrides/revoke";
        return await apiFetch<unknown>(`/v1/sales-orders/${soId}/${path}`, {
          method: "POST",
          idempotencyKey: input.key,
          body: input.body,
        });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: (_result, input) => {
      setDialog(null);
      setDone((prev) => ({ text: input.mode === "grant" ? "예외 승인을 기록했습니다." : "예외 승인을 철회했습니다.", n: (prev?.n ?? 0) + 1 }));
      grantKeys.current.clear();
      revokeKeys.current.clear();
      refresh();
      void client.invalidateQueries({ queryKey: DOCUMENT_FLOW_QUERY_KEY });
    },
    onError: (error) => {
      if (isRouteForbidden(error)) {
        setForbidden(true);
        setDialog(null);
        return;
      }
      // 실패(충돌·낡음·네트워크 등) 뒤에는 판정을 다시 읽는다 — 화면이 옛 판정을 들고 있지 않게.
      void client.invalidateQueries({ queryKey: gatesKey(soId) });
    },
  });

  function submit(mode: Mode, row: GateResult, reason: string) {
    if (lock.current) return;
    const body: GateOverrideBody = { gate_code: row.gate_code, line_id: row.line_id, basis_hash: row.basis_hash, reason };
    // 키를 먼저 만든다 — 키 생성이 던져도 잠금이 서지 않아 고착되지 않는다.
    const key = keyFor(mode === "grant" ? grantKeys.current : revokeKeys.current, JSON.stringify(body));
    lock.current = true;
    write.mutate({ mode, body, key });
  }

  /** 충돌 안내의 '최신 내용 불러오기' — 다이얼로그를 닫고 판정·SO를 다시 읽는다(입력한 사유는 사라진다). */
  function reload() {
    write.reset();
    setDialog(null);
    refresh();
  }

  function openDialog(next: DialogState) {
    write.reset();
    setDialog(next);
  }

  const data = report.data;
  const gates = Array.isArray(data?.gates) ? data.gates : null;
  // 접수가 아닌 SO(ORDER_NOT_OPEN)는 서버가 거부한다 — 화면은 버튼을 숨기고 사유를 보인다. 서버 report.status가 더 최신이다.
  const effectiveStatus = soStatus !== OPEN_STATUS ? soStatus : (data?.status ?? soStatus);
  const open = effectiveStatus === OPEN_STATUS;

  return (
    <section aria-labelledby="gate-panel-title" className="rounded-lg border border-gray-200 p-4">
      <h2 id="gate-panel-title" className="text-lg font-semibold">
        게이트 판정
      </h2>
      <p className="mt-1 break-keep text-xs text-gray-500">
        방금 조회한 참고 판정이며 확정 시 서버가 다시 평가합니다. 이 화면에서는 판정을 보고 예외 승인(override)만 다룹니다.
      </p>

      <p role="status" aria-label="처리 결과" className={done ? "mt-2 break-keep text-sm font-medium" : "sr-only"}>
        {done?.text ?? ""}
      </p>

      {report.isPending && (
        <p role="status" className="mt-3 text-sm text-gray-500">
          불러오는 중…
        </p>
      )}
      {report.error && (
        <div role="alert" className="mt-3 break-keep text-sm text-signal-red">
          <p>
            {data
              ? `최신 판정을 불러오지 못했습니다. 아래는 마지막으로 받은 값이라 낡았을 수 있습니다. ${gateLoadErrorMessage(report.error)}`
              : gateLoadErrorMessage(report.error)}
          </p>
          <button
            type="button"
            onClick={() => void report.refetch()}
            disabled={report.isFetching}
            className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1 disabled:opacity-50"
          >
            다시 시도
          </button>
        </div>
      )}
      {data !== undefined && gates === null && (
        <p className="mt-3 break-keep text-sm text-gray-500">게이트 판정을 불러오지 못했습니다.</p>
      )}

      {data !== undefined && gates !== null && (
        <>
          <ClearanceSummary report={data} summaryRef={summaryRef} />
          {data.note && <p className="mt-2 break-keep text-xs text-gray-500">{data.note}</p>}
          {data.readiness_scope_note && <p className="break-keep text-xs text-gray-500">{data.readiness_scope_note}</p>}

          {!open && (
            <p role="note" className="mt-3 break-keep rounded border border-gray-300 bg-gray-50 p-3 text-sm text-gray-700">
              접수 상태가 아닌 수주(현재: {salesOrderStatusLabel(effectiveStatus)})는 예외 승인을 부여하거나 철회할 수 없습니다.
            </p>
          )}
          {open && !roleWrite && (
            <p role="note" className="mt-3 break-keep text-sm text-gray-600">
              예외 승인·철회는 무역·관리자만 할 수 있습니다. 판정은 아래에서 확인할 수 있습니다.
            </p>
          )}

          <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
            {gates.length === 0 ? (
              <p className="p-5 text-gray-500">표시할 게이트 판정이 없습니다.</p>
            ) : (
              <table className="w-full text-sm">
                <caption className="sr-only">게이트 7종 판정 결과</caption>
                <thead className="bg-gray-50 text-left text-gray-600">
                  <tr>
                    <th scope="col" className="cell-nowrap px-3 py-2">게이트</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">결과</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">해소</th>
                    <th scope="col" className="cell-nowrap px-3 py-2">사유</th>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">처리</th>
                  </tr>
                </thead>
                <tbody>
                  {gates.map((row) => (
                    <GateRow
                      key={rowKey(row)}
                      row={row}
                      report={data}
                      open={open}
                      roleWrite={roleWrite}
                      isAdmin={isAdmin}
                      myId={me?.id ?? null}
                      me={me?.roles ?? []}
                      onGrant={() => openDialog({ mode: "grant", row })}
                      onRevoke={() => openDialog({ mode: "revoke", row })}
                    />
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {dialog !== null && (
        <ConfirmDialog
          title={dialog.mode === "grant" ? "예외 승인(override)을 부여할까요?" : "예외 승인을 철회할까요?"}
          danger
          confirmLabel={dialog.mode === "grant" ? "예외 승인 부여" : "예외 승인 철회"}
          reasonLabel={dialog.mode === "grant" ? "예외 승인 사유 (필수, 5~500자)" : "철회 사유 (필수, 5~500자)"}
          reasonMinLength={5}
          reasonMaxLength={1000}
          reasonValidator={validateOverrideReason}
          reasonHint="원가·마진·매입가 같은 민감한 값은 적지 마세요. 사유는 무역·관리자만 볼 수 있습니다."
          description={<OverrideDescription dialog={dialog} />}
          pending={write.isPending}
          error={write.error ? gateErrorMessage(write.error) : null}
          onReload={isGateRecoverable(write.error) ? reload : undefined}
          onCancel={() => {
            // 요청 중 닫기(Esc)는 무시 — 닫고 reset하면 응답을 못 본 채 같은 키로 재시도하게 된다.
            if (write.isPending) return;
            write.reset();
            setDialog(null);
          }}
          onConfirm={(reason) => submit(dialog.mode, dialog.row, reason)}
        />
      )}
    </section>
  );
}

function ClearanceSummary({ report, summaryRef }: { report: GateReport; summaryRef: React.RefObject<HTMLDivElement | null> }) {
  const { clearance, approval } = report;
  return (
    <div
      ref={summaryRef}
      tabIndex={-1}
      role="status"
      aria-label="게이트 요약"
      className="mt-3 rounded border border-gray-200 bg-gray-50 p-3 text-sm focus:outline focus:outline-2 focus:outline-gray-900"
    >
      <p className="break-keep font-medium">
        {clearance.cleared
          ? "서버 판정(참고): 모든 게이트가 해소된 상태입니다."
          : `서버 판정(참고): 미해소 게이트 ${clearance.unresolved_count}건이 있습니다.`}
      </p>
      {clearance.needs_approval && !approval.available && (
        <p className="mt-1 break-keep text-gray-700">
          여신 한도 초과로 승인(결재)이 필요합니다. 승인 요청은 확정 단계에서 제공됩니다.
        </p>
      )}
      {approval.available && approval.approval_id !== null && (
        <p className="mt-1 break-keep text-gray-700">
          사용할 수 있는 승인이 있습니다.{" "}
          <Link to={`/approvals/${approval.approval_id}`} className="cell-nowrap underline">
            승인 #{approval.approval_id} 보기
          </Link>
        </p>
      )}
    </div>
  );
}

function GateRow({
  row,
  report,
  open,
  roleWrite,
  isAdmin,
  myId,
  me,
  onGrant,
  onRevoke,
}: {
  row: GateResult;
  report: GateReport;
  open: boolean;
  roleWrite: boolean;
  isAdmin: boolean;
  myId: number | null;
  me: string[];
  onGrant: () => void;
  onRevoke: () => void;
}) {
  const target = gateTargetLabel(row);
  // 서버가 can_override=false를 줬는데 내 역할이 허용 역할이면 사유는 철회 이력(재부여는 ADMIN만)이다 — 서버 규칙(regrant_allowed)과 같은 단일 출처의 결과를 문구로만 옮긴다.
  const myRoleCanOverride = me.some((role) => row.override_roles.includes(role));
  const policies = report.policies ?? {};
  const unresolved = row.settlement === "UNRESOLVED";
  const canRevoke = open && roleWrite && row.override !== null && (isAdmin || row.override.granted_by_id === myId);
  const otherDoc = typeof row.detail?.other_doc_number === "string" ? row.detail.other_doc_number : null;
  const otherStatus = typeof row.detail?.other_status === "string" ? row.detail.other_status : null;
  // 여신 수치는 정수 최소단위뿐이라 화면에서 표시하지 않는다(*_text 계약 없음) — 마스킹 역할은 서버가 근거·해시를 비워 보낸다.
  const creditMasked = row.gate_code === "CREDIT" && row.basis_hash === "";

  return (
    <tr className="border-t border-gray-100 align-top">
      <td className="px-3 py-2">
        <span className="cell-nowrap font-medium">{gateLabel(row.gate_code)}</span>
        {row.line_id !== null && <span className="cell-nowrap block text-xs text-gray-500">라인 {row.line_no ?? "?"}</span>}
      </td>
      <td className="cell-nowrap px-3 py-2 text-center">
        <span className={`rounded border px-2 py-0.5 text-xs ${levelBadgeClass(row.level)}`}>{levelLabel(row.level)}</span>
      </td>
      <td className="px-3 py-2 text-center">
        <span className="cell-nowrap block text-xs text-gray-500">해소 수단: {resolutionLabel(row.resolution)}</span>
        <span className={`cell-nowrap mt-1 block ${unresolved ? "font-medium text-signal-red" : ""}`}>
          {settlementLabel(row.settlement)}
        </span>
      </td>
      <td className="min-w-48 break-keep px-3 py-2">
        <p>{row.message_ko}</p>
        {row.gate_code === "PI_DEPOSIT" && policies[PI_MODE_KEY] !== undefined && (
          <PolicyLine
            label="PI 입금 게이트 모드"
            value={piModeLabel(policies[PI_MODE_KEY].value)}
            unset={policies[PI_MODE_KEY].source === POLICY_UNSET}
            unsetNote={`미설정 — 기본값(${piModeLabel(policies[PI_MODE_KEY].value)})이 적용 중입니다. 정책 설정에서 저장하세요.`}
          />
        )}
        {row.gate_code === "PRICE_DEVIATION" && policies[PRICE_TOLERANCE_KEY] !== undefined && (
          <PolicyLine
            label="가격 편차 허용치"
            value={`${toleranceLabel(policies[PRICE_TOLERANCE_KEY].value)}(1bp = 0.01%)`}
            unset={policies[PRICE_TOLERANCE_KEY].source === POLICY_UNSET}
            unsetNote={`미설정 — 기본값(${toleranceLabel(policies[PRICE_TOLERANCE_KEY].value)})이 적용 중입니다. 정책 설정에서 저장하세요.`}
          />
        )}
        {row.gate_code === "CREDIT" && (
          <p className="mt-1 text-xs text-gray-600">
            여신 한도 초과는 예외 승인(override)으로 해소할 수 없고 승인(결재)으로만 해소됩니다.
            {creditMasked
              ? " 여신 수치(한도·노출)는 무역·관리자에게만 표시됩니다."
              : " 여신 수치는 승인 상세에서 확인하세요."}
          </p>
        )}
        {otherDoc !== null && (
          <p className="mt-1 text-xs text-gray-600">
            같은 PO번호의 다른 수주: <span className="cell-nowrap">{otherDoc}</span>
            {otherStatus !== null && ` (${salesOrderStatusLabel(otherStatus)})`}
          </p>
        )}
        {row.override !== null && (
          <p className="mt-1 text-xs text-gray-600">
            예외 승인: {roleLabel(row.override.authorized_role)} 권한 · <span className="cell-nowrap">{toKstDisplay(row.override.created_at)}</span>
            {row.override.reason !== null && <> · 사유: {row.override.reason}</>}
          </p>
        )}
      </td>
      <td className="cell-nowrap px-3 py-2 text-center">
        {open && roleWrite && row.can_override && (
          <button
            type="button"
            onClick={onGrant}
            aria-label={`${target} 예외 승인(override)`}
            className="cell-nowrap rounded border border-gray-900 px-3 py-1 text-sm"
          >
            예외 승인(override)
          </button>
        )}
        {canRevoke && (
          <button
            type="button"
            onClick={onRevoke}
            aria-label={`${target} 예외 승인 철회`}
            className="cell-nowrap rounded border border-signal-red px-3 py-1 text-sm text-signal-red"
          >
            철회
          </button>
        )}
        {open && roleWrite && row.override !== null && !canRevoke && (
          <span className="block max-w-40 break-keep text-xs text-gray-500">철회는 부여한 본인 또는 관리자만 할 수 있습니다.</span>
        )}
        {open && roleWrite && !row.can_override && unresolved && row.resolution === "OVERRIDE" && (
          <span className="block max-w-40 break-keep text-xs text-gray-500">
            {myRoleCanOverride
              ? "철회된 항목은 관리자만 다시 예외 승인할 수 있습니다."
              : row.override_roles.length > 0
                ? `${row.override_roles.map(roleLabel).join("·")} 역할만 예외 승인할 수 있습니다.`
                : "예외 승인할 수 없는 상태입니다."}
          </span>
        )}
      </td>
    </tr>
  );
}

function PolicyLine({ label, value, unset, unsetNote }: { label: string; value: string; unset: boolean; unsetNote: string }) {
  return (
    <p className={`mt-1 text-xs ${unset ? "font-medium text-signal-red" : "text-gray-600"}`}>
      {label}: <span className="cell-nowrap">{value}</span>
      {unset && ` — ${unsetNote}`}
    </p>
  );
}

function OverrideDescription({ dialog }: { dialog: DialogState }) {
  const { row, mode } = dialog;
  return (
    <div className="grid gap-2">
      <p>
        대상: <strong>{gateTargetLabel(row)}</strong>
      </p>
      <p>
        현재 판정: <span className={`rounded border px-2 py-0.5 text-xs ${levelBadgeClass(row.level)}`}>{levelLabel(row.level)}</span>{" "}
        {row.message_ko}
      </p>
      {mode === "grant" ? (
        <>
          <p>
            이 예외 승인은 <strong>방금 확인한 이 판정에만</strong> 유효합니다. 수량·가격·입금 등 판정 입력이 바뀌면 자동으로 무효가 되고, 확정 시 서버가 다시
            평가합니다.
          </p>
          <p>
            되돌리려면 <strong>철회</strong>해야 합니다. 철회한 뒤의 재부여는 관리자만 할 수 있습니다.
          </p>
        </>
      ) : (
        <p>
          철회하면 이 항목은 다시 미해소 상태가 됩니다. 철회한 뒤의 재부여는 <strong>관리자만</strong> 할 수 있습니다. 철회 기록은 남고 삭제되지 않습니다.
        </p>
      )}
    </div>
  );
}
