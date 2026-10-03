// 승인 상세 (S3-1 PR-9b — design-C C4·C6·C8 / 승인 통제 프런트 규칙).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - 승인·반려 버튼은 서버가 준 `can_decide`가 true인 REQUESTED에서만 보인다. 역할로 추정하지 않는다(ADMIN은 hasRole이 늘 true).
//   본인이 기안한 승인에는 ADMIN이라도 버튼이 없고 안내만 보인다('본인이 요청한 승인은 직접 결정할 수 없습니다').
// - 회수는 서버가 준 `can_withdraw`로만(기안자, 서버가 허용한 관리자). 종결 상태(반려·회수·사용됨·무효)는 읽기 전용이다.
// - 모든 쓰기는 화면이 본 기준 version(baseVersion)을 싣는다. 서버 version이 앞서가면 '다른 곳에서 수정됨' 배너+불러오기.
// - 확인 다이얼로그의 멱등 키는 여는 순간 1개 — 본문(→키 Map)이 실제로 달라질 때만 새 키, 더블클릭은 동기 잠금(ref)으로 1회,
//   409는 다이얼로그 안에서 재조회. 창 포커스 재조회로 결정 불가가 되면 다이얼로그를 닫고 안내한다.
// - 열람권 없음(403)·없는 승인(404)은 같은 안내로 — 존재 여부를 알리지 않는다.
// - 스냅샷 근거는 서버 값 그대로(산술 0).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { ApprovalSnapshot } from "../components/approval-snapshot";
import { ConfirmDialog } from "../components/confirm-dialog";
import { DocField } from "../components/proforma-facts";
import { StatusTimeline } from "../components/status-timeline";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import { decisionErrorMessage } from "../lib/approval";
import {
  APPROVALS_QUERY_KEY,
  DECISION_VERBS,
  NOT_APPROVER_NOTICE,
  NO_ACCESS_NOTICE,
  SELF_DECISION_NOTICE,
  approvalDetailKey,
  approvalStatusLabel,
  approvalTargetRoute,
  approvalTypeLabel,
  approverRoleLabel,
  moneyText,
  voidReasonText,
  type ApprovalView,
  type DecisionVerb,
} from "../lib/approval";
import { toKstDisplay } from "../lib/datetime";
import { useCurrencies } from "../lib/money";
import { useSession } from "../lib/session";
import { ApprovalStatusBadge } from "./approvals";

const NOUN = "승인";

interface DecisionBody {
  verb: DecisionVerb;
  version: number;
  reason?: string;
}

/** 승인 대상이 바뀌면 화면 상태 전체를 새로 시작한다 — 이전 승인의 기준 version·다이얼로그가 남지 않게. */
export function ApprovalDetailPage() {
  const params = useParams();
  return <ApprovalDetailView key={params.approvalId} />;
}

function ApprovalDetailView() {
  const params = useParams();
  const id = Number(params.approvalId);
  const { me } = useSession();
  const client = useQueryClient();
  const currencies = useCurrencies();

  const detailKey = approvalDetailKey(id);
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => apiFetch<ApprovalView>(`/v1/approvals/${id}`),
    enabled: Number.isInteger(id) && id > 0,
    staleTime: 0,
  });

  const [action, setAction] = useState<DecisionVerb | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  // ★ 화면이 "마지막으로 본/내가 쓴" version — 창 포커스 재조회로 서버 version이 앞서가도 쓰기는 이 값으로 보낸다.
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const submitLock = useRef(false);
  const openKey = useRef("");
  const keysByBody = useRef(new Map<string, string>());

  const loadedVersion = detail.data?.version;
  useEffect(() => {
    if (baseVersion === null && loadedVersion !== undefined) setBaseVersion(loadedVersion);
  }, [baseVersion, loadedVersion]);

  function afterWrite(next: ApprovalView, message: string) {
    client.setQueryData(detailKey, next);
    setBaseVersion(next.version);
    setInfo(message);
    // 결재함·내 요청·배지·이력이 같은 접두 키라 한 번에 갱신된다.
    void client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
  }

  const actionRef = useRef<DecisionVerb | null>(null);
  actionRef.current = action;

  const decide = useMutation({
    mutationFn: (input: { body: DecisionBody; key: string }) =>
      apiFetch<ApprovalView>(`/v1/approvals/${id}/decisions`, {
        method: "POST",
        idempotencyKey: input.key,
        body: input.body,
      }),
    // ★ 잠금 해제는 훅 수준 onSettled — mutate()에 건 콜백은 reset()으로 옵저버가 떨어지면 호출되지 않아 잠금이 영구 고착된다.
    onSettled: () => {
      submitLock.current = false;
    },
    onError: (error) => {
      // 요청 중에 창 포커스 재조회로 다이얼로그가 닫혔다면 실패가 조용히 사라지지 않게 화면에 알린다.
      if (actionRef.current === null) {
        setInfo(`결정 요청이 실패했습니다 — ${decisionErrorMessage(error)} 최신 상태를 확인해 주세요.`);
      }
    },
    onSuccess: (result, input) => {
      setAction(null);
      afterWrite(
        result,
        input.body.verb === "APPROVE"
          ? "승인했습니다."
          : input.body.verb === "REJECT"
            ? "반려했습니다."
            : "회수했습니다.",
      );
    },
  });

  function openAction(next: DecisionVerb) {
    openKey.current = crypto.randomUUID();
    keysByBody.current = new Map();
    decide.reset();
    setInfo(null);
    setAction(next);
  }

  function closeAction() {
    if (decide.isPending) return;
    decide.reset();
    setAction(null);
  }

  function reload() {
    setInfo(null);
    if (!decide.isPending) decide.reset();
    setAction(null);
    void detail.refetch().then((result) => {
      if (result.data) setBaseVersion(result.data.version);
      void client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
    });
  }

  /** 동기 잠금 — mutation의 isPending은 한 틱 늦게 켜져 같은 틱의 두 번째 클릭이 새어 나간다. */
  function submit(body: DecisionBody) {
    if (submitLock.current) return;
    submitLock.current = true;
    // 멱등 request_body에는 verb·reason·version이 들어간다 — 본문이 실제로 달라질 때만 새 키, 같은 본문 재시도는 같은 키.
    const serialized = JSON.stringify(body);
    let key = keysByBody.current.get(serialized);
    if (key === undefined) {
      key = keysByBody.current.size === 0 ? openKey.current : crypto.randomUUID();
      keysByBody.current.set(serialized, key);
    }
    decide.mutate({ body, key });
  }

  const live = detail.data;
  const isRequester = live !== undefined && me !== null && live.requested_by_id === me.id;
  // 승인·반려: 서버 자격 + REQUESTED. 본인 기안은 서버가 이미 막지만 화면도 한 번 더 닫는다(방어 — 열기만 닫는다).
  const inFlight = decide.isPending;
  const decideOpen = live !== undefined && live.status === "REQUESTED" && live.can_decide && !isRequester;
  const withdrawOpen = live !== undefined && live.can_withdraw;

  // 창 포커스 재조회로 상태·자격이 바뀌어 열려 있던 다이얼로그의 동작이 더는 가능하지 않으면 조용히 사라지지 않게 안내한다.
  useEffect(() => {
    if (action === null || live === undefined) return;
    const allowed = action === "WITHDRAW" ? withdrawOpen : decideOpen;
    if (!allowed) {
      // 요청이 나가 있으면 옵저버를 떼지 않는다(결과 콜백이 살아 있어야 잠금 해제·실패 안내가 된다).
      if (!decide.isPending) decide.reset();
      setAction(null);
      setInfo("다른 곳에서 승인 상태가 바뀌어 열려 있던 확인창을 닫았습니다. 최신 상태를 확인해 주세요.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [action, decideOpen, withdrawOpen]);

  if (detail.isPending) return <p className="p-5 text-gray-500">불러오는 중…</p>;
  if (detail.error || !detail.data) {
    const hidden = detail.error instanceof ApiError && (detail.error.status === 404 || detail.error.status === 403);
    return (
      <section>
        <p role="alert" className="break-keep text-signal-red">
          {hidden ? NO_ACCESS_NOTICE : errorMessage(detail.error, "승인을 불러오지 못했습니다.", NOUN)}
        </p>
        <Link to="/approvals" className="mt-3 inline-block text-sm underline">
          승인 목록으로
        </Link>
      </section>
    );
  }

  const approval = detail.data;
  const base = baseVersion ?? approval.version;
  const stale = approval.version !== base;
  const target = approvalTargetRoute(approval.target_type, approval.target_id);
  const money = moneyText(approval.basis_amount, approval.basis_currency, currencies.data?.items);
  const retryable =
    decide.error instanceof ApiError && (decide.error.status === 409 || decide.error.status === 403);
  const staleTitle = stale ? "다른 곳에서 수정되었습니다. 먼저 '최신 내용 불러오기'를 누르세요." : undefined;
  // 통화표를 못 불러왔으면(로딩·실패) 금액이 '…'로 남는다 — 금액을 못 본 채 승인하지 않게 승인만 막는다(반려·회수는 허용).
  const currencyMissing = currencies.data === undefined;
  const voidText = voidReasonText(approval.void_reason_code);

  return (
    <section>
      <Link to="/approvals" className="cell-nowrap text-sm text-gray-500 underline">
        ← 승인 목록
      </Link>
      <header className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">
            <span className="cell-nowrap">{`승인 #${approval.id}`}</span>
            <ApprovalStatusBadge status={approval.status} />
          </h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            {approvalTypeLabel(approval.approval_type)} — 요청 시점의 근거가 동결되어 있습니다. 결정은 이 화면에서 근거를 확인한
            뒤에 합니다.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={reload}
            className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm"
          >
            최신 내용 불러오기
          </button>
          {decideOpen && (
            <>
              <button
                type="button"
                onClick={() => openAction("APPROVE")}
                disabled={stale || inFlight}
                title={staleTitle}
                className="cell-nowrap rounded border border-gray-900 bg-gray-900 px-3 py-2 text-sm text-white disabled:opacity-50"
              >
                승인
              </button>
              <button
                type="button"
                onClick={() => openAction("REJECT")}
                disabled={stale || inFlight}
                title={staleTitle}
                className="cell-nowrap rounded border border-signal-red px-3 py-2 text-sm text-signal-red disabled:opacity-50"
              >
                반려
              </button>
            </>
          )}
          {withdrawOpen && (
            <button
              type="button"
              onClick={() => openAction("WITHDRAW")}
              disabled={stale || inFlight}
              title={staleTitle}
              className="cell-nowrap rounded border border-gray-400 px-3 py-2 text-sm disabled:opacity-50"
            >
              회수
            </button>
          )}
        </div>
      </header>

      {approval.status === "REQUESTED" && (approval.decide_blocked_reason === "SELF" || isRequester) && (
        <p role="status" className="mt-4 break-keep rounded border border-gray-400 p-3 text-sm">
          {SELF_DECISION_NOTICE}
          {withdrawOpen ? " 요청을 거두려면 '회수'를 사용하세요." : ""}
        </p>
      )}
      {approval.status === "REQUESTED" && approval.decide_blocked_reason === "NOT_APPROVER" && !isRequester && (
        <p role="status" className="mt-4 break-keep rounded border border-gray-400 p-3 text-sm">
          {NOT_APPROVER_NOTICE}
        </p>
      )}

      {info !== null && (
        <div role="status" className="mt-4 break-keep rounded border border-gray-400 p-3 text-sm">
          {info}
        </div>
      )}

      {stale && (
        <div role="status" className="mt-4 rounded border border-gray-400 p-3 text-sm">
          <p className="break-keep">
            다른 곳에서 이 승인이 수정되었습니다. 아래 내용은 최신이지만 결정 버튼은 최신 내용을 확인한 뒤에 열립니다.
          </p>
          <button
            type="button"
            onClick={reload}
            className="cell-nowrap mt-2 rounded border border-gray-400 px-3 py-1"
          >
            최신 내용 불러오기
          </button>
        </div>
      )}

      {approval.status === "VOIDED" && (
        <div role="status" className="mt-4 break-keep rounded border border-signal-red p-3 text-sm text-signal-red">
          <p className="font-medium">이 승인은 무효입니다. 읽기 전용입니다.</p>
          {voidText && <p className="mt-1">{voidText}</p>}
          {approval.status_reason && <p className="mt-1">사유: {approval.status_reason}</p>}
        </div>
      )}
      {(approval.status === "REJECTED" || approval.status === "WITHDRAWN") && (
        <div role="status" className="mt-4 break-keep rounded border border-gray-400 p-3 text-sm">
          <p className="font-medium">
            {approvalStatusLabel(approval.status)}된 승인입니다. 읽기 전용이며, 다시 진행하려면 새로 요청해야 합니다.
          </p>
          {approval.status_reason && <p className="mt-1">사유: {approval.status_reason}</p>}
        </div>
      )}
      {approval.status === "CONSUMED" && (
        <p role="status" className="mt-4 break-keep rounded border border-gray-400 p-3 text-sm">
          이미 수주 확정에 사용된 승인입니다. 읽기 전용입니다.
        </p>
      )}

      <div className="mt-6 grid grid-cols-[minmax(0,1fr)] gap-6">
        <section aria-label="승인 정보" className="rounded-lg border border-gray-200 p-4">
          <h2 className="text-lg font-semibold">승인 정보</h2>
          <dl className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <DocField label="대상">
              {target === null ? (
                approval.target_label
              ) : (
                <Link to={target} className="underline">
                  {approval.target_label}
                </Link>
              )}
            </DocField>
            <DocField label="요청자">{approval.requester_name ?? "알 수 없음"}</DocField>
            <DocField label="요청일">{approval.created_at ? toKstDisplay(approval.created_at) : "—"}</DocField>
            <DocField label="필요 결재 역할">{approverRoleLabel(approval.required_role)}</DocField>
            <DocField label="승인 기준 금액">
              <span className="num block text-center">{money}</span>
            </DocField>
            {approval.decided_at !== null && (
              <DocField label="결정">
                {approval.decided_by_name ?? "알 수 없음"}
                {approval.decided_on_behalf_of_name ? ` (대결 — 위임자 ${approval.decided_on_behalf_of_name})` : ""}
                {` · ${toKstDisplay(approval.decided_at)}`}
              </DocField>
            )}
            {approval.consumed_at !== null && (
              <DocField label="사용 일시">{toKstDisplay(approval.consumed_at)}</DocField>
            )}
          </dl>
        </section>

        <ApprovalSnapshot approval={approval} />

        <StatusTimeline
          basePath={`/v1/approvals/${approval.id}`}
          logPath={`/v1/approvals/${approval.id}/events`}
          queryKey={detailKey}
          statusLabel={approvalStatusLabel}
        />
      </div>

      {action !== null && (
        <ConfirmDialog
          title={`${approval.target_label} — ${DECISION_VERBS[action].title}`}
          danger={action !== "APPROVE"}
          confirmLabel={DECISION_VERBS[action].confirm}
          reasonLabel={DECISION_VERBS[action].reason ?? undefined}
          description={
            action === "APPROVE" ? (
              <p>
                승인하면 요청자가 이 수주를 확정할 수 있게 되며, 승인은 <strong>1회만</strong> 쓰입니다. 승인 기준 금액은{" "}
                <span className="num">{money}</span>입니다. 근거(미수 반영 여부 포함)를 확인하셨나요?
                {currencyMissing && (
                  <span role="alert" className="mt-2 block text-signal-red">
                    금액 표시에 필요한 통화 정보를 불러오지 못했습니다 — 새로고침하세요. 금액을 확인할 수 없어 승인할 수 없습니다.
                  </span>
                )}
              </p>
            ) : action === "REJECT" ? (
              <p>반려하면 이 승인은 종결되고 되돌릴 수 없습니다. 요청자는 사유를 확인한 뒤 새로 요청해야 합니다.</p>
            ) : (
              <p>회수하면 이 승인은 종결되고 되돌릴 수 없습니다. 다시 진행하려면 새로 요청해야 합니다.</p>
            )
          }
          confirmDisabled={action === "APPROVE" && currencyMissing}
          reasonMaxLength={1000}
          pending={decide.isPending}
          error={decide.error ? decisionErrorMessage(decide.error) : null}
          onReload={retryable || isVersionConflict(decide.error) ? reload : undefined}
          onCancel={closeAction}
          onConfirm={(reason) =>
            submit({ verb: action, version: base, ...(action === "APPROVE" ? {} : { reason }) })
          }
        />
      )}
    </section>
  );
}
