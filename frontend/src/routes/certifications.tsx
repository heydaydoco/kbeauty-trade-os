// 인증 인스턴스 화면 (DESIGN.md §5.1·§5.2 / §14 ③·④ 최소분 / S2-2 PR-1).
//
// ★ 상태는 전이 버튼으로만 바뀐다 — 전이 표 밖은 서버가 409로 거부한다(§5.2).
//   여기의 HUMAN_TRANSITIONS는 UX용 사본이고 정본은 서버 machine.py다(사본이
//   낡아도 서버가 막는다). 칸반+캘린더 보드는 S2-3 몫(판정 ⑪ — WBS v1.4).
// ★ 편집(등록·전이·태스크)은 인증+관리자다(판정 ⑫) — 화면 게이트는 표시일 뿐
//   실제 차단은 서버(§18.1).
// ★ 드롭다운(템플릿·대상)은 전부 ?size=200 우회다 — 대상(SKU 수백 규모)의
//   200 상한 한계는 관찰 원장 등재분(트리거: 200건 초과 실측 시 검색형 UI).
// ★ 날짜·상태 표시는 셀에서 전제를 확인하고 그린다(함정 ⑦ — 던질 수 있는
//   표시 함수를 가드 없이 부르면 화면 백지).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { apiDelete, apiFetch } from "../lib/api";
import { toKstDisplay } from "../lib/datetime";
import { appliesToLabel, certificationStatusLabel, orEmpty } from "../lib/labels";
import { usePagedList, usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { fieldMessage } from "./brands";
import type { DocumentRow } from "./documents";
import type { RequirementTemplate } from "./requirement-templates";

export interface Certification {
  id: number;
  template_id: number;
  market_code: string;
  template_name: string;
  requirement_type: string;
  target_type: string;
  target_id: number | null;
  target_label: string;
  status: string;
  validity_months: number | null;
  renewal_cycle_months: number | null;
  renewal_lead_days: number | null;
  source_url: string | null;
  last_verified_on: string | null;
  cert_number: string | null;
  applied_on: string | null;
  approved_on: string | null;
  valid_from: string | null;
  expires_on: string | null;
  assignee_id: number | null;
  note: string | null;
  version: number;
}

interface CertificationTask {
  id: number;
  certification_id: number;
  seq: number;
  item_name: string;
  document_type_id: number | null;
  is_required: boolean;
  done: boolean;
  done_at: string | null;
  document_id: number | null;
  note: string | null;
  version: number;
}

interface StatusLogEntry {
  id: number;
  certification_id: number;
  occurred_at: string;
  from_status: string;
  to_status: string;
  reason: string | null;
  actor_user_id: number | null;
  expires_on_snapshot: string | null;
  cert_number_snapshot: string | null;
}

interface Prerequisite {
  id: number;
  template_id: number;
  prerequisite_template_id: number;
  prerequisite_name: string;
  prerequisite_status: string;
  version: number;
}

/** 대상 드롭다운 소비용 최소 모양 — 목록마다 이름 필드가 다르다. */
interface TargetOption {
  id: number;
  name_ko?: string | null;
  inci_name?: string;
}

export const CERTIFICATIONS_QUERY_KEY = ["certifications"] as const;

const STATUS_OPTIONS = [
  "NOT_STARTED",
  "PREPARING",
  "SUBMITTED",
  "IN_REVIEW",
  "SUPPLEMENTING",
  "APPROVED",
  "EXPIRING",
  "RENEWING",
  "REJECTED",
  "EXPIRED",
  "SUSPENDED",
] as const;

// 사람 전이 21방향의 UX 사본 — 정본은 서버 machine.HUMAN_TRANSITIONS(§5.2).
const HUMAN_TRANSITIONS: Record<string, string[]> = {
  NOT_STARTED: ["PREPARING", "SUSPENDED"],
  PREPARING: ["SUBMITTED", "SUSPENDED"],
  SUBMITTED: ["IN_REVIEW", "SUSPENDED"],
  IN_REVIEW: ["SUPPLEMENTING", "APPROVED", "REJECTED", "SUSPENDED"],
  SUPPLEMENTING: ["IN_REVIEW", "REJECTED", "SUSPENDED"],
  APPROVED: ["RENEWING", "SUSPENDED"],
  EXPIRING: ["RENEWING", "SUSPENDED"],
  RENEWING: ["APPROVED", "SUSPENDED"],
  EXPIRED: ["RENEWING", "SUSPENDED"],
  REJECTED: [],
  SUSPENDED: [],
};

/** 대상 유형별 선택 목록 경로 — COMPANY는 자사 단일이라 목록이 없다. */
const TARGET_LIST_PATH: Record<string, string | null> = {
  PRODUCT: "/v1/products?size=200",
  SKU: "/v1/skus?size=200",
  INGREDIENT: "/v1/ingredients?size=200",
  FACILITY: "/v1/partners?size=200",
  COMPANY: null,
};

function targetOptionLabel(option: TargetOption): string {
  return option.name_ko ?? option.inci_name ?? `#${option.id}`;
}

function orUndefined(value: string): string | undefined {
  return value.trim() === "" ? undefined : value.trim();
}

/** 전이 폼 — 도달 상태에 따라 부속 필드가 갈린다(§17.1 같은 요청·같은 트랜잭션). */
function TransitionPanel({
  row,
  onDone,
}: {
  row: Certification;
  onDone: (fresh: Certification) => void;
}) {
  const client = useQueryClient();
  const targets = HUMAN_TRANSITIONS[row.status] ?? [];
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [dates, setDates] = useState({
    applied_on: "",
    approved_on: "",
    valid_from: "",
    expires_on: "",
    cert_number: "",
  });

  const transition = useMutation({
    mutationFn: () =>
      apiFetch<Certification>(`/v1/certifications/${row.id}/transitions`, {
        method: "POST",
        body: {
          to,
          version: row.version,
          reason: orUndefined(reason),
          ...(to === "SUBMITTED" ? { applied_on: orUndefined(dates.applied_on) } : {}),
          ...(to === "APPROVED"
            ? {
                approved_on: orUndefined(dates.approved_on),
                valid_from: orUndefined(dates.valid_from),
                expires_on: orUndefined(dates.expires_on),
                cert_number: orUndefined(dates.cert_number),
              }
            : {}),
        },
      }),
    onSuccess: (fresh) => {
      setTo("");
      setReason("");
      setDates({ applied_on: "", approved_on: "", valid_from: "", expires_on: "", cert_number: "" });
      void client.invalidateQueries({ queryKey: CERTIFICATIONS_QUERY_KEY });
      onDone(fresh);
    },
  });

  const message = fieldMessage(transition.error);
  if (targets.length === 0) {
    return (
      <p className="mt-2 text-sm text-gray-500">
        종결 상태입니다 — 재도전은 새 인스턴스로 등록합니다(같은 대상·템플릿 재등록이 열려
        있습니다).
      </p>
    );
  }

  const reasonRequired = to === "REJECTED" || to === "SUSPENDED";

  return (
    <form
      className="mt-3 flex flex-wrap items-end gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        transition.mutate();
      }}
    >
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">다음 상태</span>
        <select
          name="to"
          required
          value={to}
          onChange={(event) => setTo(event.target.value)}
          className="rounded border border-gray-300 px-3 py-2"
        >
          <option value="">선택</option>
          {targets.map((code) => (
            <option key={code} value={code}>
              {certificationStatusLabel(code)}
            </option>
          ))}
        </select>
      </label>
      {to === "SUBMITTED" && (
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">신청일</span>
          <input
            name="applied_on"
            required
            type="date"
            value={dates.applied_on}
            onChange={(event) =>
              setDates((prev) => ({ ...prev, applied_on: event.target.value }))
            }
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
      )}
      {to === "APPROVED" && (
        <>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">승인일</span>
            <input
              name="approved_on"
              required={row.status === "IN_REVIEW"}
              type="date"
              value={dates.approved_on}
              onChange={(event) =>
                setDates((prev) => ({ ...prev, approved_on: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">유효 시작일</span>
            <input
              name="valid_from"
              required={row.status === "IN_REVIEW"}
              type="date"
              value={dates.valid_from}
              onChange={(event) =>
                setDates((prev) => ({ ...prev, valid_from: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">만료일 (무기한이면 비움)</span>
            <input
              name="expires_on"
              type="date"
              value={dates.expires_on}
              onChange={(event) =>
                setDates((prev) => ({ ...prev, expires_on: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">인증번호</span>
            <input
              name="cert_number"
              maxLength={100}
              value={dates.cert_number}
              onChange={(event) =>
                setDates((prev) => ({ ...prev, cert_number: event.target.value }))
              }
              className="w-44 rounded border border-gray-300 px-3 py-2"
            />
          </label>
        </>
      )}
      <label className="flex flex-col gap-1 text-sm">
        <span className="cell-nowrap text-gray-600">
          사유{reasonRequired ? " (필수)" : " (선택)"}
        </span>
        <input
          name="reason"
          required={reasonRequired}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          className="w-64 rounded border border-gray-300 px-3 py-2"
        />
      </label>
      <button
        type="submit"
        disabled={transition.isPending}
        className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
      >
        전이
      </button>
      {message && (
        <p role="alert" className="w-full text-sm text-signal-red">
          {message}
        </p>
      )}
    </form>
  );
}

/** 연결된 서류 셀 — 문서가 목록 밖(200 상한·삭제)이어도 던지지 않는다(함정 ⑦ 가드). */
function TaskDocumentCell({
  documentId,
  document,
}: {
  documentId: number | null;
  document: DocumentRow | undefined;
}) {
  if (documentId === null) return <>{orEmpty(null)}</>;
  if (document === undefined) return <>{`#${documentId}`}</>;
  if (document.storage_kind === "FILE") {
    return (
      <a href={`/api/v1/documents/${document.id}/download`} className="underline">
        {orEmpty(document.original_filename)}
      </a>
    );
  }
  if (document.url) {
    return (
      <a href={document.url} className="underline" rel="noreferrer noopener">
        링크 열기
      </a>
    );
  }
  return <>{orEmpty(null)}</>;
}

function taskDocumentOptionLabel(doc: DocumentRow): string {
  return `#${doc.id} ${doc.document_type_name} · ${doc.original_filename ?? doc.url ?? ""}`;
}

/** 태스크 패널 — 체크 토글·추가·제거·서류 링크 (ADR-11 "태스크는 자유" / §5.1). */
function TasksPanel({ row, canEdit }: { row: Certification; canEdit: boolean }) {
  const client = useQueryClient();
  const listKey = ["certifications", row.id, "tasks"] as const;
  const tasks = usePagedQuery<CertificationTask>(listKey, `/v1/certifications/${row.id}/tasks`);
  // 연결 후보·표시용 문서 목록 — 드롭다운 선례대로 ?size=200 우회(§5.6 공용
  // 참조라 소유자 필터를 걸지 않는다). 200 상한 한계는 관찰 원장 계열.
  const documents = usePagedQuery<DocumentRow>(
    ["documents", "task-link-options"],
    "/v1/documents?size=200",
  );
  const documentById = new Map((documents.data?.items ?? []).map((doc) => [doc.id, doc]));
  const [form, setForm] = useState({ seq: "", item_name: "", required: true });

  const toggle = useMutation({
    mutationFn: (task: CertificationTask) =>
      apiFetch<CertificationTask>(`/v1/certifications/${row.id}/tasks/${task.id}`, {
        method: "PATCH",
        body: { version: task.version, done: !task.done },
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: listKey }),
  });

  const linkDocument = useMutation({
    mutationFn: ({ task, documentId }: { task: CertificationTask; documentId: number | null }) =>
      apiFetch<CertificationTask>(`/v1/certifications/${row.id}/tasks/${task.id}`, {
        method: "PATCH",
        body: { version: task.version, document_id: documentId },
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: listKey }),
  });

  const add = useMutation({
    mutationFn: () =>
      apiFetch<CertificationTask>(`/v1/certifications/${row.id}/tasks`, {
        method: "POST",
        body: { seq: Number(form.seq), item_name: form.item_name, is_required: form.required },
      }),
    onSuccess: () => {
      setForm({ seq: "", item_name: "", required: true });
      void client.invalidateQueries({ queryKey: listKey });
    },
  });

  const remove = useMutation({
    mutationFn: (taskId: number) => apiDelete(`/v1/certifications/${row.id}/tasks/${taskId}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: listKey }),
  });

  const message =
    fieldMessage(toggle.error) ??
    fieldMessage(linkDocument.error) ??
    fieldMessage(add.error) ??
    fieldMessage(remove.error);

  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h3 className="font-semibold">태스크</h3>
      <p className="mt-1 text-sm text-gray-500">
        템플릿 체크리스트에서 복사된 준비 항목입니다. 이 인증에만 필요한 항목은 자유롭게
        추가하세요.
      </p>
      <ListState
        isPending={tasks.isPending}
        error={tasks.error}
        isEmpty={tasks.data?.items.length === 0}
        emptyHint="태스크가 없습니다."
      >
        <table className="mt-3 w-full text-sm">
          <thead className="bg-gray-50 text-left">
            <tr>
              <th className="cell-nowrap px-3 py-2 num">순번</th>
              <th className="px-3 py-2">항목</th>
              <th className="cell-nowrap px-3 py-2">필수</th>
              <th className="px-3 py-2">서류</th>
              <th className="cell-nowrap px-3 py-2">완료</th>
              {canEdit && <th className="px-3 py-2" />}
            </tr>
          </thead>
          <tbody>
            {tasks.data?.items.map((task) => (
              <tr key={task.id} className="border-t border-gray-100">
                <td className="cell-nowrap px-3 py-2 num">{task.seq}</td>
                <td className="px-3 py-2">{task.item_name}</td>
                <td className="cell-nowrap px-3 py-2">{task.is_required ? "필수" : "선택"}</td>
                <td className="px-3 py-2">
                  {canEdit && (
                    <select
                      aria-label={`${task.item_name} 서류 연결`}
                      value={task.document_id ?? ""}
                      disabled={linkDocument.isPending}
                      onChange={(event) =>
                        linkDocument.mutate({
                          task,
                          documentId:
                            event.target.value === "" ? null : Number(event.target.value),
                        })
                      }
                      className="mr-2 max-w-56 rounded border border-gray-300 px-2 py-1 text-xs"
                    >
                      <option value="">(연결 없음)</option>
                      {documents.data?.items.map((doc) => (
                        <option key={doc.id} value={doc.id}>
                          {taskDocumentOptionLabel(doc)}
                        </option>
                      ))}
                      {/* 현재 연결 문서가 목록 밖이어도 값이 사라지지 않게 유지한다. */}
                      {task.document_id !== null && !documentById.has(task.document_id) && (
                        <option value={task.document_id}>{`#${task.document_id}`}</option>
                      )}
                    </select>
                  )}
                  <TaskDocumentCell
                    documentId={task.document_id}
                    document={
                      task.document_id === null ? undefined : documentById.get(task.document_id)
                    }
                  />
                </td>
                <td className="cell-nowrap px-3 py-2">
                  {canEdit ? (
                    <input
                      type="checkbox"
                      aria-label={`${task.item_name} 완료`}
                      checked={task.done}
                      disabled={toggle.isPending}
                      onChange={() => toggle.mutate(task)}
                    />
                  ) : task.done ? (
                    "완료"
                  ) : (
                    "미완료"
                  )}
                  {/* 완료 시각은 셀에서 전제 확인 후 표시한다(함정 ⑦). */}
                  {task.done && task.done_at !== null && (
                    <span className="ml-2 text-xs text-gray-500">
                      {toKstDisplay(task.done_at)}
                    </span>
                  )}
                </td>
                {canEdit && (
                  <td className="cell-nowrap px-3 py-2">
                    <button
                      type="button"
                      onClick={() => remove.mutate(task.id)}
                      disabled={remove.isPending}
                      className="rounded border border-gray-300 px-2 py-1 text-xs"
                    >
                      제거
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </ListState>
      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">순번</span>
            <input
              name="seq"
              required
              type="number"
              min={1}
              value={form.seq}
              onChange={(event) => setForm((prev) => ({ ...prev, seq: event.target.value }))}
              className="w-20 rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">항목 이름</span>
            <input
              name="item_name"
              required
              maxLength={200}
              value={form.item_name}
              onChange={(event) => setForm((prev) => ({ ...prev, item_name: event.target.value }))}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input
              name="is_required"
              type="checkbox"
              checked={form.required}
              onChange={(event) => setForm((prev) => ({ ...prev, required: event.target.checked }))}
            />
            <span className="cell-nowrap">필수 항목</span>
          </label>
          <button
            type="submit"
            disabled={add.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            태스크 추가
          </button>
        </form>
      )}
      {message && (
        <p role="alert" className="mt-2 text-sm text-signal-red">
          {message}
        </p>
      )}
    </div>
  );
}

/** 상태 변경 이력 — 불변 기록(§17.5 확장). 행위자 없음=시스템(날짜 자동 전이). */
function HistoryPanel({ row }: { row: Certification }) {
  const history = usePagedQuery<StatusLogEntry>(
    ["certifications", row.id, "status-log"],
    `/v1/certifications/${row.id}/status-log`,
  );
  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h3 className="font-semibold">상태 변경 이력</h3>
      <ListState
        isPending={history.isPending}
        error={history.error}
        isEmpty={history.data?.items.length === 0}
        emptyHint="이력이 없습니다 — 아직 전이가 없었습니다."
      >
        <table className="mt-3 w-full text-sm">
          <thead className="bg-gray-50 text-left">
            <tr>
              <th className="cell-nowrap px-3 py-2">시각</th>
              <th className="cell-nowrap px-3 py-2">전이</th>
              <th className="px-3 py-2">사유</th>
              <th className="cell-nowrap px-3 py-2">행위자</th>
            </tr>
          </thead>
          <tbody>
            {history.data?.items.map((entry) => (
              <tr key={entry.id} className="border-t border-gray-100">
                <td className="cell-nowrap px-3 py-2">{toKstDisplay(entry.occurred_at)}</td>
                <td className="cell-nowrap px-3 py-2">
                  {certificationStatusLabel(entry.from_status)} →{" "}
                  {certificationStatusLabel(entry.to_status)}
                </td>
                <td className="px-3 py-2">{orEmpty(entry.reason)}</td>
                <td className="cell-nowrap px-3 py-2">
                  {entry.actor_user_id === null ? "시스템(자동)" : `사용자 #${entry.actor_user_id}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </ListState>
    </div>
  );
}

/** 선행요건 참고 표시 (판정 ⑬ — 차단 없음·판정 워딩 금지. 사실 서술만). */
function PrerequisiteHint({ row }: { row: Certification }) {
  const prerequisites = usePagedQuery<Prerequisite>(
    ["certifications", row.template_id, "prerequisites"],
    `/v1/requirement-templates/${row.template_id}/prerequisites`,
  );
  const siblings = usePagedQuery<Certification>(
    ["certifications", "by-target", row.target_type, row.target_id],
    `/v1/certifications?target_type=${row.target_type}&size=200`,
  );
  if (prerequisites.data === undefined || prerequisites.data.items.length === 0) {
    return null;
  }
  const byTemplate = new Map<number, Certification[]>();
  for (const sibling of siblings.data?.items ?? []) {
    if (sibling.target_id === row.target_id) {
      const bucket = byTemplate.get(sibling.template_id) ?? [];
      bucket.push(sibling);
      byTemplate.set(sibling.template_id, bucket);
    }
  }
  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h3 className="font-semibold">선행요건 참고</h3>
      <p className="mt-1 text-sm text-gray-500">
        이 요건의 템플릿에 등록된 선행요건과, 같은 대상의 해당 인증 진행 상황입니다. 참고
        표시이며 전이를 막지 않습니다.
      </p>
      <ul className="mt-3 flex flex-col gap-1 text-sm">
        {prerequisites.data.items.map((entry) => {
          const matches = byTemplate.get(entry.prerequisite_template_id) ?? [];
          return (
            <li key={entry.id} className="flex flex-wrap items-center gap-2">
              <span className="cell-nowrap font-medium">{entry.prerequisite_name}</span>
              {matches.length === 0 ? (
                <span className="text-gray-500">이 대상의 인증 인스턴스 없음</span>
              ) : (
                matches.map((match) => (
                  <span key={match.id} className="cell-nowrap rounded-full border border-gray-300 px-2 py-0.5">
                    {certificationStatusLabel(match.status)}
                  </span>
                ))
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function CertificationsPage() {
  const { me } = useSession();
  const client = useQueryClient();
  const canEdit = hasRole(me, "CERT");

  const [form, setForm] = useState({ template_id: "", target_id: "", note: "" });
  const [statusFilter, setStatusFilter] = useState("");
  const [selected, setSelected] = useState<Certification | null>(null);

  const listPath =
    statusFilter === "" ? "/v1/certifications" : `/v1/certifications?status=${statusFilter}`;
  const list = usePagedList<Certification>(
    [...CERTIFICATIONS_QUERY_KEY, { status: statusFilter }],
    listPath,
  );
  // 확정 템플릿만 — 생성 게이트(안건 ①)와 같은 경계를 화면도 따른다.
  const templates = usePagedQuery<RequirementTemplate>(
    ["requirement-templates", "confirmed"],
    "/v1/requirement-templates?size=200&status=CONFIRMED",
    canEdit,
  );
  const chosenTemplate =
    templates.data?.items.find((t) => String(t.id) === form.template_id) ?? null;
  const targetPath = chosenTemplate ? TARGET_LIST_PATH[chosenTemplate.applies_to] : null;
  const targetOptions = usePagedQuery<TargetOption>(
    ["certifications", "targets", chosenTemplate?.applies_to ?? "none"],
    targetPath ?? "/v1/certifications?size=1",
    canEdit && targetPath !== null,
  );

  const register = useMutation({
    mutationFn: () =>
      apiFetch<Certification>("/v1/certifications", {
        method: "POST",
        body: {
          template_id: Number(form.template_id),
          target_type: chosenTemplate?.applies_to ?? "",
          target_id: targetPath === null ? undefined : Number(form.target_id),
          note: orUndefined(form.note),
        },
      }),
    onSuccess: () => {
      setForm({ template_id: "", target_id: "", note: "" });
      void client.invalidateQueries({ queryKey: CERTIFICATIONS_QUERY_KEY });
    },
  });

  const registerMessage = fieldMessage(register.error);

  return (
    <section>
      <header>
        <h1 className="text-2xl font-bold">인증 인스턴스</h1>
        <p className="mt-1 text-sm text-gray-500">
          대상×요건 템플릿의 인증 진행 대장입니다. 상태는 전이 버튼으로만 바뀌고, 만료임박·만료는
          만료일 기준으로 자동 부여됩니다. 확정된 템플릿에서만 등록할 수 있습니다.
        </p>
      </header>

      {canEdit && (
        <form
          className="mt-6 rounded-lg border border-gray-200 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate();
          }}
        >
          <h2 className="font-semibold">새 인증 등록</h2>
          <div className="mt-3 flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">요건 템플릿 (확정분만)</span>
              <select
                name="template_id"
                required
                value={form.template_id}
                onChange={(event) =>
                  setForm((prev) => ({ ...prev, template_id: event.target.value, target_id: "" }))
                }
                className="rounded border border-gray-300 px-3 py-2"
              >
                <option value="">선택</option>
                {templates.data?.items.map((template) => (
                  <option key={template.id} value={template.id}>
                    [{template.market_code}] {template.name} ({appliesToLabel(template.applies_to)})
                  </option>
                ))}
              </select>
            </label>
            {chosenTemplate !== null && targetPath !== null && (
              <label className="flex flex-col gap-1 text-sm">
                <span className="cell-nowrap text-gray-600">
                  대상 ({appliesToLabel(chosenTemplate.applies_to)})
                </span>
                <select
                  name="target_id"
                  required
                  value={form.target_id}
                  onChange={(event) =>
                    setForm((prev) => ({ ...prev, target_id: event.target.value }))
                  }
                  className="rounded border border-gray-300 px-3 py-2"
                >
                  <option value="">선택</option>
                  {targetOptions.data?.items.map((option) => (
                    <option key={option.id} value={option.id}>
                      {targetOptionLabel(option)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {chosenTemplate !== null && targetPath === null && (
              <p className="pb-2 text-sm text-gray-500">기업 단위 요건 — 대상은 자사입니다.</p>
            )}
            <label className="flex flex-col gap-1 text-sm">
              <span className="cell-nowrap text-gray-600">메모</span>
              <input
                name="note"
                value={form.note}
                onChange={(event) => setForm((prev) => ({ ...prev, note: event.target.value }))}
                className="w-72 rounded border border-gray-300 px-3 py-2"
              />
            </label>
            <button
              type="submit"
              disabled={register.isPending}
              className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              등록
            </button>
          </div>
          {registerMessage && (
            <p role="alert" className="mt-3 text-sm text-signal-red">
              {registerMessage}
            </p>
          )}
        </form>
      )}

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          <span className="cell-nowrap text-gray-600">상태 필터</span>
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="">전체</option>
            {STATUS_OPTIONS.map((code) => (
              <option key={code} value={code}>
                {certificationStatusLabel(code)}
              </option>
            ))}
          </select>
        </label>
        <ListPager data={list.data} page={list.page} onPageChange={list.setPage} />
      </div>

      <div className="mt-3 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={
            canEdit
              ? "등록된 인증이 없습니다. 위에서 확정 템플릿과 대상을 골라 등록하세요."
              : "등록된 인증이 없습니다."
          }
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="cell-nowrap px-4 py-2">시장</th>
                <th className="px-4 py-2">요건</th>
                <th className="px-4 py-2">대상</th>
                <th className="cell-nowrap px-4 py-2">상태</th>
                <th className="cell-nowrap px-4 py-2">만료일</th>
                <th className="cell-nowrap px-4 py-2">인증번호</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((row) => (
                <tr key={row.id} className="border-t border-gray-100">
                  <td className="cell-nowrap px-4 py-2">{row.market_code}</td>
                  <td className="px-4 py-2">{row.template_name}</td>
                  <td className="px-4 py-2">{row.target_label}</td>
                  <td className="cell-nowrap px-4 py-2">
                    {certificationStatusLabel(row.status)}
                  </td>
                  <td className="cell-nowrap px-4 py-2">{orEmpty(row.expires_on)}</td>
                  <td className="cell-nowrap px-4 py-2">{orEmpty(row.cert_number)}</td>
                  <td className="cell-nowrap px-4 py-2">
                    <button
                      type="button"
                      onClick={() => setSelected((prev) => (prev?.id === row.id ? null : row))}
                      className="text-sm text-gray-700 underline"
                    >
                      {selected?.id === row.id ? "상세 닫기" : "상세"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {selected && (
        <div className="mt-6 rounded-lg border border-gray-300 p-4">
          <h2 className="font-semibold">
            [{selected.market_code}] {selected.template_name} — {selected.target_label}
          </h2>
          <p className="mt-1 text-sm text-gray-600">
            상태 {certificationStatusLabel(selected.status)}
            {selected.expires_on !== null && ` · 만료일 ${selected.expires_on}`}
            {selected.cert_number !== null && ` · 인증번호 ${selected.cert_number}`}
          </p>
          {canEdit && (
            <TransitionPanel
              row={selected}
              onDone={(fresh) => setSelected(fresh)}
            />
          )}
          <PrerequisiteHint row={selected} />
          <TasksPanel row={selected} canEdit={canEdit} />
          <HistoryPanel row={selected} />
        </div>
      )}
    </section>
  );
}
