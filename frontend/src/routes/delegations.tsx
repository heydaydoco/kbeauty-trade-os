// 대결(위임) 관리 (S3-1 PR-9b — design-C C5 / 승인 통제 규칙 ⑥).
//
// - 대결 = 부재 중 내 결재 역할을 다른 사람에게 기간(KST 날짜, 양끝 포함)으로 맡기는 설정이다. 소급 등록은 서버가 거절한다.
// - 내가 위임한 건·나에게 위임된 건·(관리자) 전체. 종료(조기 종료)는 위임자 본인 또는 관리자 — 서버가 준 `can_revoke`로만 보인다.
// - 수임자는 서버 후보 검색(delegation-candidates)에서 고른다(SearchSelect — 전체 목록을 받아 자르지 않는다).
// - 서버 거절 사유(본인 지정·조회 전용 수임자·미보유 역할·기간 겹침·소급)는 서버 한국어 문구를 그대로 안내한다.
// - 대결로 승인할 때도 기안자 본인의 요청은 결재할 수 없다(결정 시점 직무분리 — 서버가 판정, 승인 상세가 안내한다).
// - 등록은 멱등 키(폼 진입 1개·본문이 달라질 때만 새 키), 종료는 version(낙관 잠금).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ConfirmDialog } from "../components/confirm-dialog";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { SearchSelect } from "../components/search-select";
import { ApiError, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import {
  APPROVALS_QUERY_KEY,
  APPROVAL_TYPE_CODES,
  APPROVER_ROLE_CODES,
  DELEGATIONS_QUERY_KEY,
  approvalTypeLabel,
  approverRoleLabel,
  delegationBadgeClass,
  delegationStateLabel,
  inertReasonText,
  type Candidate,
  type Delegation,
} from "../lib/approval";
import { todayKst } from "../lib/datetime";
import { usePagedList } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

const NOUN = "대결";
const CANDIDATES_KEY = ["approvals", "delegation-candidates"] as const;

export function DelegationsPage() {
  const { me } = useSession();
  const isAdmin = hasRole(me);
  const client = useQueryClient();
  const [scope, setScope] = useState<"mine" | "all">("mine");
  const list = usePagedList<Delegation>(
    [...DELEGATIONS_QUERY_KEY, "list", scope],
    `/v1/delegations?scope=${scope}`,
    true,
    { staleTime: 0 },
  );
  const [info, setInfo] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<Delegation | null>(null);

  // 수임자의 결재함 배지(승인 접두 키)도 같이 갱신한다 — 대결 등록·종료가 결재 가능 범위를 바꾼다.
  const refresh = () => {
    void client.invalidateQueries({ queryKey: DELEGATIONS_QUERY_KEY });
    void client.invalidateQueries({ queryKey: APPROVALS_QUERY_KEY });
  };
  const items = list.data?.items ?? [];
  const mineOut = items.filter((d) => d.delegator_user_id === me?.id);
  const mineIn = items.filter((d) => d.delegate_user_id === me?.id && d.delegator_user_id !== me?.id);
  const others = scope === "all" ? items.filter((d) => !mineOut.includes(d) && !mineIn.includes(d)) : [];

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">대결 설정</h1>
        <p className="mt-1 break-keep text-sm text-gray-500">
          부재 기간 동안 내 결재 역할을 다른 사람이 대신 결재하도록 맡깁니다. 기간은 시작일·종료일을 포함하고, 이미 지난 날짜로는 등록할
          수 없습니다. 대결로도 본인이 요청한 승인은 결재할 수 없습니다.
        </p>
      </header>

      {info !== null && (
        <p role="status" className="mt-3 break-keep rounded border border-gray-400 p-3 text-sm">
          {info}
        </p>
      )}

      <CreateForm
        isAdmin={isAdmin}
        onCreated={() => {
          setInfo("대결을 등록했습니다.");
          refresh();
        }}
      />

      {isAdmin && (
        <label className="mt-4 flex items-center gap-2 text-sm">
          <span className="cell-nowrap text-gray-600">보기 범위</span>
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as "mine" | "all")}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="mine">내 대결</option>
            <option value="all">전체 (관리자)</option>
          </select>
        </label>
      )}

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-4" />

      <ListState
        isPending={list.isPending}
        error={list.error}
        isEmpty={list.data?.items.length === 0}
        emptyHint="등록된 대결이 없습니다. 부재 전에 위 양식으로 등록하세요."
      >
        <DelegationTable title="내가 위임한 대결" rows={mineOut} onRevoke={setRevoking} />
        <DelegationTable title="나에게 위임된 대결" rows={mineIn} onRevoke={setRevoking} />
        {scope === "all" && <DelegationTable title="다른 사용자 간 대결" rows={others} onRevoke={setRevoking} />}
      </ListState>

      {revoking !== null && (
        <RevokeDialog
          delegation={revoking}
          onClose={() => setRevoking(null)}
          onRevoked={() => {
            setRevoking(null);
            setInfo("대결을 종료했습니다.");
            refresh();
          }}
        />
      )}
    </section>
  );
}

function DelegationTable({
  title,
  rows,
  onRevoke,
}: {
  title: string;
  rows: Delegation[];
  onRevoke: (d: Delegation) => void;
}) {
  return (
    <section aria-label={title} className="mt-4">
      <h2 className="text-lg font-semibold">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-2 text-sm text-gray-500">해당하는 대결이 없습니다.</p>
      ) : (
        <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
          <table className="w-full min-w-[52rem] text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-3 py-2">위임자</th>
                <th className="cell-nowrap px-3 py-2">수임자</th>
                <th className="cell-nowrap px-3 py-2">유형</th>
                <th className="cell-nowrap px-3 py-2">역할</th>
                <th className="cell-nowrap px-3 py-2 text-center">기간</th>
                <th className="cell-nowrap px-3 py-2">상태</th>
                <th className="cell-nowrap px-3 py-2">관리</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr key={d.id} className="border-t border-gray-100 align-top">
                  <td className="cell-nowrap px-3 py-2">{d.delegator_name ?? "알 수 없음"}</td>
                  <td className="cell-nowrap px-3 py-2">{d.delegate_name ?? "알 수 없음"}</td>
                  <td className="break-keep px-3 py-2">{approvalTypeLabel(d.approval_type)}</td>
                  <td className="cell-nowrap px-3 py-2">{approverRoleLabel(d.delegated_role)}</td>
                  <td className="cell-nowrap num px-3 py-2 text-center">
                    {d.start_on} ~ {d.end_on}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`cell-nowrap rounded border px-2 py-0.5 text-xs ${delegationBadgeClass(d.state)}`}>
                      {delegationStateLabel(d.state)}
                    </span>
                    {d.state === "INERT" && (
                      <p className="mt-1 break-keep text-xs text-signal-red">{inertReasonText(d.inert_reason)}</p>
                    )}
                    {d.note && <p className="mt-1 break-keep text-xs text-gray-500">{d.note}</p>}
                  </td>
                  <td className="px-3 py-2">
                    {d.can_revoke && (
                      <button
                        type="button"
                        onClick={() => onRevoke(d)}
                        className="cell-nowrap rounded border border-signal-red px-2 py-1 text-signal-red"
                      >
                        종료
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ── 등록 ─────────────────────────────────────────────────────────────────────

function CreateForm({ isAdmin, onCreated }: { isAdmin: boolean; onCreated: () => void }) {
  const [delegate, setDelegate] = useState<Candidate | null>(null);
  const [delegator, setDelegator] = useState<Candidate | null>(null);
  const [type, setType] = useState(APPROVAL_TYPE_CODES[0] ?? "");
  const [role, setRole] = useState("");
  // 기본값은 KST 오늘 — 브라우저 시간대가 아니라 업무 날짜 기준이다(소급 거절선과 같다).
  const [start, setStart] = useState(() => todayKst());
  const [end, setEnd] = useState(() => todayKst());
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [proxyAck, setProxyAck] = useState(false);
  const lock = useRef(false);
  const openKey = useRef(crypto.randomUUID());
  const keysByBody = useRef(new Map<string, string>());

  const create = useMutation({
    mutationFn: (input: { body: Record<string, unknown>; key: string }) =>
      apiFetch<Delegation>("/v1/delegations", { method: "POST", body: input.body, idempotencyKey: input.key }),
    onSuccess: () => {
      setDelegate(null);
      setDelegator(null);
      setProxyAck(false);
      setNote("");
      openKey.current = crypto.randomUUID();
      keysByBody.current = new Map();
      onCreated();
    },
  });

  function submit() {
    if (lock.current) return;
    if (delegate === null) return setProblem("대신 결재할 사람(수임자)을 선택해 주세요.");
    if (role === "") return setProblem("위임할 결재 역할을 선택해 주세요.");
    if (delegator !== null && delegator.id === delegate.id) return setProblem("위임자와 수임자가 같을 수 없습니다.");
    if (delegator !== null && !proxyAck) return setProblem("타인 명의 대리 등록 안내를 확인하고 체크해 주세요.");
    if (start === "" || end === "") return setProblem("시작일과 종료일을 입력해 주세요.");
    if (start < todayKst()) return setProblem("과거 날짜로는 대결을 등록할 수 없습니다. 시작일을 오늘 이후로 입력해 주세요.");
    if (end < start) return setProblem("종료일은 시작일 이후여야 합니다.");
    setProblem(null);
    const body: Record<string, unknown> = {
      delegate_user_id: delegate.id,
      approval_type: type,
      delegated_role: role,
      start_on: start,
      end_on: end,
      ...(note.trim() !== "" ? { note: note.trim() } : {}),
      ...(isAdmin && delegator !== null ? { delegator_user_id: delegator.id } : {}),
    };
    const serialized = JSON.stringify(body);
    let key = keysByBody.current.get(serialized);
    if (key === undefined) {
      key = keysByBody.current.size === 0 ? openKey.current : crypto.randomUUID();
      keysByBody.current.set(serialized, key);
    }
    lock.current = true;
    create.mutate({ body, key }, { onSettled: () => (lock.current = false) });
  }

  const pending = create.isPending;
  return (
    <form
      aria-label="대결 등록"
      className="mt-4 rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <h2 className="text-lg font-semibold">대결 등록</h2>
      <fieldset disabled={pending} className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <div className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">수임자 (대신 결재할 사람)</span>
          <SearchSelect<Candidate>
            label="수임자"
            path="/v1/approvals/delegation-candidates"
            queryKey={CANDIDATES_KEY}
            value={delegate}
            onChange={setDelegate}
            getKey={(c) => c.id}
            getLabel={(c) => c.display_name}
            placeholder="이름으로 검색"
            disabled={pending}
          />
        </div>
        {isAdmin && (
          <div className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">위임자 (관리자 대리 등록 — 비우면 본인 명의)</span>
            <SearchSelect<Candidate>
              label="위임자"
              path="/v1/approvals/delegation-candidates"
              queryKey={CANDIDATES_KEY}
              value={delegator}
              onChange={setDelegator}
              getKey={(c) => c.id}
              getLabel={(c) => c.display_name}
              placeholder="이름으로 검색"
              disabled={pending}
            />
            {delegator === null ? (
              <span className="break-keep text-xs text-gray-500">선택하지 않으면 본인 명의로 등록됩니다.</span>
            ) : (
              <label className="flex items-start gap-2 break-keep text-xs text-signal-red">
                <input type="checkbox" checked={proxyAck} onChange={(e) => setProxyAck(e.target.checked)} className="mt-0.5" />
                <span>
                  관리자가 {delegator.display_name} 님 명의로 대결 권한을 부여합니다 — 감사 기록에 남습니다. 확인했습니다.
                </span>
              </label>
            )}
          </div>
        )}
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">승인 유형</span>
          <select value={type} onChange={(e) => setType(e.target.value)} className="rounded border border-gray-300 px-3 py-2">
            {APPROVAL_TYPE_CODES.map((code) => (
              <option key={code} value={code}>
                {approvalTypeLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">위임할 결재 역할</span>
          <select value={role} onChange={(e) => setRole(e.target.value)} className="rounded border border-gray-300 px-3 py-2">
            <option value="">선택</option>
            {APPROVER_ROLE_CODES.map((code) => (
              <option key={code} value={code}>
                {approverRoleLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">시작일</span>
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">종료일</span>
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">메모 (선택)</span>
          <input
            value={note}
            maxLength={200}
            onChange={(e) => setNote(e.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <div className="flex items-end">
          <button type="submit" className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-50">
            {pending ? "등록 중…" : "등록"}
          </button>
        </div>
      </fieldset>
      {(problem !== null || create.error) && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {problem ?? errorMessage(create.error, undefined, NOUN)}
        </p>
      )}
    </form>
  );
}

// ── 종료 ─────────────────────────────────────────────────────────────────────

function RevokeDialog({
  delegation,
  onClose,
  onRevoked,
}: {
  delegation: Delegation;
  onClose: () => void;
  onRevoked: () => void;
}) {
  const client = useQueryClient();
  const lock = useRef(false);
  const revoke = useMutation({
    mutationFn: () =>
      apiFetch<Delegation>(`/v1/delegations/${delegation.id}/revoke`, {
        method: "POST",
        body: { version: delegation.version },
      }),
    onSuccess: onRevoked,
  });
  const conflict = revoke.error instanceof ApiError && revoke.error.status === 409;
  return (
    <ConfirmDialog
      title="대결을 종료할까요?"
      danger
      confirmLabel="대결 종료"
      description={
        <p>
          {delegation.delegator_name} → {delegation.delegate_name} ({delegation.start_on} ~ {delegation.end_on}) 대결을 지금
          종료합니다. 종료하면 수임자는 더 이상 이 역할로 결재할 수 없고, 되돌릴 수 없습니다(다시 필요하면 새로 등록).
        </p>
      }
      pending={revoke.isPending}
      error={revoke.error ? errorMessage(revoke.error, undefined, NOUN) : null}
      onReload={
        isVersionConflict(revoke.error) || conflict
          ? () => {
              void client.invalidateQueries({ queryKey: DELEGATIONS_QUERY_KEY });
              onClose();
            }
          : undefined
      }
      onCancel={() => {
        if (!revoke.isPending) onClose();
      }}
      onConfirm={() => {
        if (lock.current) return;
        lock.current = true;
        revoke.mutate(undefined, { onSettled: () => (lock.current = false) });
      }}
    />
  );
}
