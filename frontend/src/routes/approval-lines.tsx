// 결재선 관리 (S3-1 PR-9b — design-C C3 / 승인 통제 규칙 ⑦).
//
// - 조회는 비조회 4역할, 등록·수정·삭제는 ADMIN 전용(서버 authz_matrix와 같다 — 화면은 편의, 서버가 403으로 다시 막는다).
// - 1행 = "이 유형·통화에서 금액이 임계를 **초과**하면 이 역할이 결재한다". 임계는 사람 표기(12.34)로 보내고, 서버가 최소단위로 바꾼다.
// - ★ fail-closed: 결재선이 없으면 승인 요청이 거절되어 여신 초과 수주를 확정할 수 없다(정상 동작 — 안내 배너).
// - 수정은 역할·메모만(유형·통화·임계는 삭제 후 신규), 진행 중인 승인에는 소급되지 않는다.
// - 쓰기: 등록은 멱등 키(폼 진입 1개·본문이 달라질 때만 새 키), 수정·삭제는 행 version(낙관 잠금) — 409는 불러오기 안내.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { ConfirmDialog } from "../components/confirm-dialog";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiDelete, apiFetch } from "../lib/api";
import { errorMessage, isVersionConflict } from "../lib/api-errors";
import {
  APPROVAL_LINES_QUERY_KEY,
  APPROVAL_TYPE_CODES,
  APPROVER_ROLE_CODES,
  approvalTypeLabel,
  approverRoleLabel,
  type ApprovalLine,
  type Coverage,
} from "../lib/approval";
import { useCurrencies } from "../lib/money";
import { usePagedList } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";

const NOUN = "결재선";
const THRESHOLD_PATTERN = /^\d{1,13}(\.\d{1,6})?$/;

export function ApprovalLinesPage() {
  const { me } = useSession();
  const isAdmin = hasRole(me);
  const client = useQueryClient();
  const list = usePagedList<ApprovalLine>([...APPROVAL_LINES_QUERY_KEY, "list"], "/v1/approval-lines", true, {
    staleTime: 0,
  });
  const coverage = useQuery({
    queryKey: [...APPROVAL_LINES_QUERY_KEY, "coverage"],
    queryFn: () => apiFetch<Coverage>("/v1/approval-lines/coverage"),
    staleTime: 0,
  });
  const [notice, setNotice] = useState<unknown>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<ApprovalLine | null>(null);

  const refresh = () => void client.invalidateQueries({ queryKey: APPROVAL_LINES_QUERY_KEY });

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">결재선</h1>
        <p className="mt-1 break-keep text-sm text-gray-500">
          여신 한도를 넘는 수주는 아래 구간에 따라 해당 역할이 결재합니다. 금액이 임계를 초과하면 그 역할이 결재하며, 변경은 이미
          요청된 승인에는 적용되지 않습니다.
        </p>
      </header>

      <div
        role="status"
        className={`mt-4 break-keep rounded border p-3 text-sm ${
          coverage.data && !coverage.data.configured ? "border-signal-red text-signal-red" : "border-gray-300 text-gray-700"
        }`}
      >
        <p className="font-medium">결재선이 없으면 승인 요청이 거절됩니다.</p>
        <p className="mt-1">
          결재선이 없는 유형·통화의 여신 초과 수주는 확정할 수 없습니다(정상 동작 — 관리자가 결재선을 등록해야 합니다).
        </p>
        {coverage.data?.messages.map((message) => (
          <p key={message} className="mt-1">
            {message}
          </p>
        ))}
        {coverage.error !== null && <p className="mt-1">결재선 공급 현황을 불러오지 못했습니다.</p>}
      </div>

      {info !== null && (
        <p role="status" className="mt-3 break-keep rounded border border-gray-400 p-3 text-sm">
          {info}
        </p>
      )}
      {notice !== null && (
        <div role="alert" className="mt-3 rounded border border-signal-red p-3 text-sm text-signal-red">
          <p className="break-keep">{errorMessage(notice, undefined, NOUN)}</p>
          {isVersionConflict(notice) && (
            <button
              type="button"
              onClick={() => {
                setNotice(null);
                refresh();
              }}
              className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1"
            >
              최신 내용 불러오기
            </button>
          )}
        </div>
      )}

      {isAdmin ? (
        <CreateForm
          onCreated={() => {
            setNotice(null);
            setInfo("결재선을 등록했습니다.");
            refresh();
          }}
        />
      ) : (
        <p className="mt-4 break-keep text-sm text-gray-500">결재선은 관리자만 등록·수정·삭제할 수 있습니다.</p>
      )}

      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-4" />
      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint="등록된 결재선이 없습니다. 결재선이 없으면 승인 요청이 거절되어 여신 초과 수주를 확정할 수 없습니다."
        >
          <table className="w-full min-w-[44rem] text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="cell-nowrap px-3 py-2">유형</th>
                <th className="cell-nowrap px-3 py-2">통화</th>
                <th className="cell-nowrap px-3 py-2 text-center">임계 금액 (초과 시)</th>
                <th className="cell-nowrap px-3 py-2">결재 역할</th>
                <th className="cell-nowrap px-3 py-2">메모</th>
                {isAdmin && <th className="cell-nowrap px-3 py-2">관리</th>}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((line) => (
                <LineRow
                  key={`${line.id}-${line.version}`}
                  line={line}
                  isAdmin={isAdmin}
                  onSaved={() => {
                    setNotice(null);
                    setInfo("결재선을 수정했습니다. 이미 요청된 승인에는 적용되지 않습니다.");
                    refresh();
                  }}
                  onError={setNotice}
                  onDelete={() => setDeleting(line)}
                />
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {deleting !== null && (
        <DeleteLineDialog
          line={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            setDeleting(null);
            setNotice(null);
            setInfo("결재선을 삭제했습니다.");
            refresh();
          }}
        />
      )}
    </section>
  );
}

// ── 등록 ─────────────────────────────────────────────────────────────────────

function CreateForm({ onCreated }: { onCreated: () => void }) {
  const currencies = useCurrencies();
  const [type, setType] = useState(APPROVAL_TYPE_CODES[0] ?? "");
  const [currency, setCurrency] = useState("");
  const [threshold, setThreshold] = useState("");
  const [role, setRole] = useState("");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const lock = useRef(false);
  const openKey = useRef(crypto.randomUUID());
  const keysByBody = useRef(new Map<string, string>());

  const create = useMutation({
    mutationFn: (input: { body: Record<string, unknown>; key: string }) =>
      apiFetch<ApprovalLine>("/v1/approval-lines", { method: "POST", body: input.body, idempotencyKey: input.key }),
    onSuccess: () => {
      setThreshold("");
      setNote("");
      setRole("");
      // 새 폼 = 새 키 — 다음 등록이 이번 등록의 멱등 재생으로 오인되지 않게.
      openKey.current = crypto.randomUUID();
      keysByBody.current = new Map();
      onCreated();
    },
  });

  function submit() {
    if (lock.current) return;
    const trimmed = threshold.trim();
    if (type === "" || currency === "" || role === "") {
      setProblem("유형·통화·결재 역할을 모두 선택해 주세요.");
      return;
    }
    if (!THRESHOLD_PATTERN.test(trimmed)) {
      setProblem("임계 금액은 0 이상의 숫자로 입력해 주세요. 예: 5000 또는 5000.50 (이 금액을 초과하면 해당 역할이 결재합니다)");
      return;
    }
    setProblem(null);
    const body: Record<string, unknown> = {
      approval_type: type,
      // 사람 표기 문자열 그대로 — 최소단위 변환은 서버가 한다(프런트 산술 0).
      threshold: trimmed,
      currency,
      approver_role: role,
      ...(note.trim() !== "" ? { note: note.trim() } : {}),
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
      aria-label="결재선 등록"
      className="mt-4 rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <h2 className="text-lg font-semibold">결재선 등록</h2>
      <fieldset disabled={pending} className="mt-3 flex flex-wrap items-end gap-3 text-sm">
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
          <span className="cell-nowrap text-gray-600">통화</span>
          <select
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">선택</option>
            {currencies.data?.items.map((c) => (
              <option key={c.code} value={c.code}>
                {c.code}
              </option>
            ))}
          </select>
          {currencies.data && currencies.data.total > currencies.data.items.length && (
            <span role="status" className="text-xs text-gray-500">
              통화 {currencies.data.total}종 중 {currencies.data.items.length}종만 표시
            </span>
          )}
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">임계 금액 (초과 시)</span>
          <input
            value={threshold}
            inputMode="decimal"
            maxLength={20}
            onChange={(e) => setThreshold(e.target.value)}
            className="num w-36 rounded border border-gray-300 px-3 py-2 text-center"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">결재 역할</span>
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
          <span className="cell-nowrap text-gray-600">메모 (선택)</span>
          <input
            value={note}
            maxLength={200}
            onChange={(e) => setNote(e.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <button type="submit" className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-50">
          {pending ? "등록 중…" : "등록"}
        </button>
      </fieldset>
      {(problem !== null || create.error) && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {problem ?? errorMessage(create.error, undefined, NOUN)}
        </p>
      )}
    </form>
  );
}

// ── 행(수정) ─────────────────────────────────────────────────────────────────

function LineRow({
  line,
  isAdmin,
  onSaved,
  onError,
  onDelete,
}: {
  line: ApprovalLine;
  isAdmin: boolean;
  onSaved: () => void;
  onError: (error: unknown) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [role, setRole] = useState(line.approver_role);
  const [note, setNote] = useState(line.note ?? "");
  const lock = useRef(false);

  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiFetch<ApprovalLine>(`/v1/approval-lines/${line.id}`, { method: "PATCH", body }),
    onSuccess: () => {
      setEditing(false);
      onSaved();
    },
    onError,
  });

  const roleChanged = role !== line.approver_role;
  const noteChanged = note.trim() !== (line.note ?? "");
  // 무변경 저장 차단 — version만 올리는 빈 쓰기를 만들지 않는다.
  const changed = roleChanged || noteChanged;

  function submit() {
    if (lock.current || !changed) return;
    lock.current = true;
    const body: Record<string, unknown> = { version: line.version };
    if (roleChanged) body.approver_role = role;
    if (noteChanged) body.note = note.trim();
    save.mutate(body, { onSettled: () => (lock.current = false) });
  }

  return (
    <tr className="border-t border-gray-100 align-top">
      <td className="break-keep px-3 py-2">{approvalTypeLabel(line.approval_type)}</td>
      <td className="cell-nowrap px-3 py-2">{line.threshold_currency}</td>
      <td className="cell-nowrap num px-3 py-2 text-center">
        {line.threshold_text !== "" ? line.threshold_text : String(line.threshold_amount)}
      </td>
      <td className="px-3 py-2">
        {editing ? (
          <select
            aria-label="결재 역할"
            value={role}
            disabled={save.isPending}
            onChange={(e) => setRole(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1"
          >
            {APPROVER_ROLE_CODES.map((code) => (
              <option key={code} value={code}>
                {approverRoleLabel(code)}
              </option>
            ))}
          </select>
        ) : (
          <span className="cell-nowrap">{approverRoleLabel(line.approver_role)}</span>
        )}
      </td>
      <td className="px-3 py-2">
        {editing ? (
          <input
            aria-label="메모"
            value={note}
            maxLength={200}
            disabled={save.isPending}
            onChange={(e) => setNote(e.target.value)}
            className="w-full rounded border border-gray-300 px-2 py-1"
          />
        ) : (
          <span className="break-keep">{line.note ?? "—"}</span>
        )}
      </td>
      {isAdmin && (
        <td className="px-3 py-2">
          <div className="flex flex-wrap gap-2">
            {editing ? (
              <>
                <button
                  type="button"
                  onClick={submit}
                  disabled={!changed || save.isPending}
                  className="cell-nowrap rounded border border-gray-900 px-2 py-1 disabled:opacity-50"
                >
                  {save.isPending ? "저장 중…" : "저장"}
                </button>
                <button
                  type="button"
                  disabled={save.isPending}
                  onClick={() => {
                    setEditing(false);
                    setRole(line.approver_role);
                    setNote(line.note ?? "");
                  }}
                  className="cell-nowrap rounded border border-gray-300 px-2 py-1 disabled:opacity-50"
                >
                  취소
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="cell-nowrap rounded border border-gray-300 px-2 py-1"
                >
                  수정
                </button>
                <button
                  type="button"
                  onClick={onDelete}
                  className="cell-nowrap rounded border border-signal-red px-2 py-1 text-signal-red"
                >
                  삭제
                </button>
              </>
            )}
          </div>
        </td>
      )}
    </tr>
  );
}

// ── 삭제 ─────────────────────────────────────────────────────────────────────

function DeleteLineDialog({
  line,
  onClose,
  onDeleted,
}: {
  line: ApprovalLine;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const client = useQueryClient();
  const lock = useRef(false);
  const remove = useMutation({
    mutationFn: () => apiDelete(`/v1/approval-lines/${line.id}?version=${line.version}`),
    onSuccess: onDeleted,
  });
  return (
    <ConfirmDialog
      title="결재선을 삭제할까요?"
      danger
      confirmLabel="삭제"
      description={
        <p>
          {approvalTypeLabel(line.approval_type)} · {line.threshold_currency} · 임계 {line.threshold_text} 초과 →{" "}
          {approverRoleLabel(line.approver_role)} 결재선을 삭제합니다. 이미 요청된 승인에는 영향이 없지만, 이 구간의 새 승인
          요청은 결재선이 없어 거절될 수 있습니다.
        </p>
      }
      pending={remove.isPending}
      error={remove.error ? errorMessage(remove.error, undefined, NOUN) : null}
      onReload={
        isVersionConflict(remove.error)
          ? () => {
              void client.invalidateQueries({ queryKey: APPROVAL_LINES_QUERY_KEY });
              onClose();
            }
          : undefined
      }
      onCancel={() => {
        if (!remove.isPending) onClose();
      }}
      onConfirm={() => {
        if (lock.current) return;
        lock.current = true;
        remove.mutate(undefined, { onSettled: () => (lock.current = false) });
      }}
    />
  );
}
