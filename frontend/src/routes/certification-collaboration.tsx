// 인증 상세의 대행 협업 패널 — 비상태 필드 편집·통신 기록 (DESIGN.md §5.4 / S2-4 PR-1).
//
// ★ 상태는 전이 폼으로만 바뀐다 — 이 파일의 편집 폼에는 상태 필드가 없다(서버 PATCH 스키마에도
//   구조적으로 없다). 만료일 정정은 승인·만료임박·만료 상태에서만 서버가 받는다(409) — 그 밖에서는
//   입력칸을 잠가 보인다.
// ★ 처리방식 AGENCY ⇔ 대행사 지정, 공이 대행사에 있으려면 대행 처리여야 한다 — 서버(422)·DB가 강제하고,
//   화면은 같은 규칙으로 선택지를 좁혀 실수를 미리 막는다(사본 — 정본은 서버).
// ★ 통신 기록은 "일어난 일의 기록"이다 — 발송 기능이 없다(§5.4 "생성까지 시스템, 발송은 사람").
//   다음 액션 기한이 되면 정체·독촉 알림이 알림센터에 오른다(stagnation-scan).
// ★ 편집 권한은 인증+관리자다(화면 게이트는 표시일 뿐 — 실제 차단은 서버 §18.1).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, apiDelete, apiDownload, apiFetch, apiUpload } from "../lib/api";
import { toKstDisplay, todayKst } from "../lib/datetime";
import { actionOwnerLabel, handlingModeLabel, orEmpty } from "../lib/labels";
import { FRESH_EVERY_TIME, usePagedQuery } from "../lib/paging";
import { fieldMessage } from "./brands";
import type { Certification } from "./certifications";
import type { DocumentRow, DocumentTypeRow } from "./documents";
import { DOCUMENT_TYPES_QUERY_KEY } from "./documents";
import type { Partner } from "./partners";
import { PARTNERS_SELECT_PATH } from "./partners";

export const COMM_LOGS_QUERY_KEY = ["comm-logs"] as const;

export interface CommLog {
  id: number;
  subject_type: string;
  subject_id: number;
  subject_label: string;
  partner_id: number | null;
  partner_name: string | null;
  occurred_on: string;
  summary: string;
  next_action: string | null;
  next_action_due: string | null;
  next_action_done_on: string | null;
  /** 계산값 — 다음 액션이 남아 있는가 / 기한이 지났는가(서버 KST 오늘 기준). */
  follow_up_open: boolean;
  follow_up_overdue: boolean;
  attachment_count: number;
  created_at: string;
  version: number;
}

/** 승인·만료임박·만료 — 만료일 정정이 열리는 3태(서버 machine.DATE_DERIVED_STATUSES 사본). */
const EXPIRES_EDITABLE = ["APPROVED", "EXPIRING", "EXPIRED"];

function orNull(value: string): string | null {
  return value.trim() === "" ? null : value.trim();
}

/** 인증 사이의 거래처 선택지 — 유형이 인증대행인 거래처만(서버 검증과 같은 경계). */
function useAgencyOptions() {
  return usePagedQuery<Partner>(["partners", "agency-options"], PARTNERS_SELECT_PATH);
}

// ── 비상태 필드 편집 ─────────────────────────────────────────────────────────

/**
 * 인증번호·만료일 정정·메모·대행 협업 필드(처리방식·대행사·현재 액션 주체·공이 넘어간 날).
 * 바뀐 필드만 PATCH로 보낸다(exclude_unset — 안 보낸 필드는 서버가 건드리지 않는다).
 * 부모가 `key`에 version을 넣어 저장 뒤 새 값으로 다시 그리게 한다.
 */
export function CertificationEditPanel({
  row,
  onDone,
}: {
  row: Certification;
  onDone: (fresh: Certification) => void;
}) {
  const agencies = useAgencyOptions();
  const expiresEditable = EXPIRES_EDITABLE.includes(row.status);
  const [form, setForm] = useState({
    cert_number: row.cert_number ?? "",
    expires_on: row.expires_on ?? "",
    note: row.note ?? "",
    handling_mode: row.handling_mode,
    agency_partner_id: row.agency_partner_id === null ? "" : String(row.agency_partner_id),
    action_owner: row.action_owner,
    action_owner_changed_on: "",
  });

  // 바뀐 필드만 — 서버는 안 보낸 필드를 건드리지 않는다. 처리방식·대행사·주체는 서로 얽혀 있어
  // (AGENCY ⇔ 대행사) 어느 하나가 바뀌면 일관된 조합으로 함께 보낸다.
  const partnerId = form.handling_mode === "AGENCY" ? Number(form.agency_partner_id) : null;
  const body: Record<string, unknown> = { version: row.version };
  if (form.cert_number !== (row.cert_number ?? "")) body.cert_number = orNull(form.cert_number);
  if (form.note !== (row.note ?? "")) body.note = orNull(form.note);
  if (expiresEditable && form.expires_on !== (row.expires_on ?? "")) {
    body.expires_on = orNull(form.expires_on);
  }
  if (form.handling_mode !== row.handling_mode) body.handling_mode = form.handling_mode;
  if (partnerId !== row.agency_partner_id) body.agency_partner_id = partnerId;
  if (form.action_owner !== row.action_owner) body.action_owner = form.action_owner;
  if (form.action_owner_changed_on !== "") {
    body.action_owner_changed_on = form.action_owner_changed_on;
  }
  const changed = Object.keys(body).length > 1;
  const agencyMissing = form.handling_mode === "AGENCY" && form.agency_partner_id === "";

  // 목록 캐시 무효화는 부모(onDone)가 한다 — 이 파일이 certifications.tsx를 런타임 임포트하면
  // 두 모듈이 서로를 물게 된다(타입 임포트만 남긴다).
  const save = useMutation({
    mutationFn: () =>
      apiFetch<Certification>(`/v1/certifications/${row.id}`, { method: "PATCH", body }),
    onSuccess: (fresh) => onDone(fresh),
  });
  const message = fieldMessage(save.error);

  const agencyChoices = (agencies.data?.items ?? []).filter((partner) =>
    partner.type_codes.includes("CERT_AGENCY"),
  );
  // 현재 지정된 대행사가 선택지에 없더라도(200건 상한·유형 해제) 값이 사라지지 않게 한다.
  const currentInChoices = agencyChoices.some((partner) => partner.id === row.agency_partner_id);

  return (
    <form
      className="mt-4 rounded-lg border border-gray-200 p-4"
      aria-label="인증 정보 편집"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <h3 className="font-semibold">정보 편집 · 대행 협업</h3>
      <p className="mt-1 text-sm text-gray-500">
        상태는 위의 전이로만 바뀝니다. 대행 처리로 바꾸면 대행사를 지정해야 하고, 공이 누구 쪽에
        있는지 적어 두면 오래 멈춘 건을 알림센터가 알려 줍니다.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">인증번호</span>
          <input
            name="cert_number"
            maxLength={100}
            value={form.cert_number}
            onChange={(event) => setForm((prev) => ({ ...prev, cert_number: event.target.value }))}
            className="w-44 rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">만료일 정정</span>
          <input
            name="expires_on"
            type="date"
            disabled={!expiresEditable}
            value={form.expires_on}
            onChange={(event) => setForm((prev) => ({ ...prev, expires_on: event.target.value }))}
            className="rounded border border-gray-300 px-3 py-2 disabled:bg-gray-100"
          />
        </label>
        {!expiresEditable && (
          <p className="max-w-xs pb-2 text-xs text-gray-500">
            만료일은 승인 이후(승인·만료임박·만료)에만 정정할 수 있습니다. 승인 전 만료일은 승인
            기록에 함께 입력하세요.
          </p>
        )}
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">메모</span>
          <input
            name="note"
            value={form.note}
            onChange={(event) => setForm((prev) => ({ ...prev, note: event.target.value }))}
            className="w-64 rounded border border-gray-300 px-3 py-2"
          />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">처리방식</span>
          <select
            name="handling_mode"
            value={form.handling_mode}
            onChange={(event) => {
              const mode = event.target.value;
              setForm((prev) => ({
                ...prev,
                handling_mode: mode,
                // 직접 처리로 돌아가면 대행사 지정을 풀고, 공이 대행사에 있었다면 사내로 되돌린다.
                agency_partner_id: mode === "AGENCY" ? prev.agency_partner_id : "",
                action_owner:
                  mode !== "AGENCY" && prev.action_owner === "AGENCY" ? "INTERNAL" : prev.action_owner,
              }));
            }}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="DIRECT">{handlingModeLabel("DIRECT")}</option>
            <option value="AGENCY">{handlingModeLabel("AGENCY")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">대행사</span>
          <select
            name="agency_partner_id"
            disabled={form.handling_mode !== "AGENCY"}
            required={form.handling_mode === "AGENCY"}
            value={form.agency_partner_id}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, agency_partner_id: event.target.value }))
            }
            className="rounded border border-gray-300 px-3 py-2 disabled:bg-gray-100"
          >
            <option value="">선택</option>
            {row.agency_partner_id !== null && !currentInChoices && (
              <option value={row.agency_partner_id}>
                {row.agency_partner_name ?? `#${row.agency_partner_id}`}
              </option>
            )}
            {agencyChoices.map((partner) => (
              <option key={partner.id} value={partner.id}>
                {partner.name_ko}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">현재 공이 있는 곳</span>
          <select
            name="action_owner"
            value={form.action_owner}
            onChange={(event) => setForm((prev) => ({ ...prev, action_owner: event.target.value }))}
            className="rounded border border-gray-300 px-3 py-2"
          >
            <option value="INTERNAL">{actionOwnerLabel("INTERNAL")}</option>
            <option value="AGENCY" disabled={form.handling_mode !== "AGENCY"}>
              {actionOwnerLabel("AGENCY")}
            </option>
            <option value="AUTHORITY">{actionOwnerLabel("AUTHORITY")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="cell-nowrap text-gray-600">공이 넘어간 날 (선택)</span>
          <input
            name="action_owner_changed_on"
            type="date"
            max={todayKst()}
            value={form.action_owner_changed_on}
            onChange={(event) =>
              setForm((prev) => ({ ...prev, action_owner_changed_on: event.target.value }))
            }
            className="rounded border border-gray-300 px-3 py-2"
          />
        </label>
        <button
          type="submit"
          disabled={save.isPending || !changed || agencyMissing}
          className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          저장
        </button>
      </div>
      <p className="mt-2 text-xs text-gray-500">
        공이 넘어간 날은 비우면 그대로 두거나(주체를 바꾸면 오늘로 자동 기록), 실제로 넘어간 날이
        다르면 입력합니다.
      </p>
      {message && (
        <p role="alert" className="mt-3 text-sm text-signal-red">
          {message}
        </p>
      )}
    </form>
  );
}

// ── 통신 기록 ────────────────────────────────────────────────────────────────

function followUpBadge(log: CommLog) {
  if (log.next_action === null) return null;
  if (log.next_action_done_on !== null) {
    return (
      <span className="cell-nowrap rounded bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700">
        완료 {log.next_action_done_on}
      </span>
    );
  }
  if (log.follow_up_overdue) {
    return (
      <span className="cell-nowrap rounded bg-red-100 px-1.5 py-0.5 text-xs font-semibold text-red-800">
        기한 지남
      </span>
    );
  }
  return (
    <span className="cell-nowrap rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-900">
      진행 중
    </span>
  );
}

/** 통신 기록 한 건의 첨부 — 링크·파일을 문서보관소(소유 유형 COMM_LOG)에 붙인다. */
function LogAttachments({ log, canEdit }: { log: CommLog; canEdit: boolean }) {
  const client = useQueryClient();
  const attachments = usePagedQuery<DocumentRow>(
    ["documents", "comm-log", log.id],
    `/v1/documents?owner_type=COMM_LOG&owner_id=${log.id}`,
  );
  const types = usePagedQuery<DocumentTypeRow>(
    DOCUMENT_TYPES_QUERY_KEY,
    "/v1/document-types?size=200",
    canEdit,
  );
  const [form, setForm] = useState({ mode: "LINK", document_type: "", url: "" });
  const [file, setFile] = useState<File | null>(null);
  const [fileInputKey, setFileInputKey] = useState(0);

  const attach = useMutation({
    mutationFn: () => {
      const shared = { owner_type: "COMM_LOG", owner_id: log.id, document_type: form.document_type };
      if (form.mode === "LINK") {
        return apiFetch<DocumentRow>("/v1/documents/links", {
          method: "POST",
          body: { ...shared, url: form.url },
        });
      }
      const data = new FormData();
      if (file) data.append("file", file);
      for (const [key, value] of Object.entries(shared)) data.append(key, String(value));
      return apiUpload<DocumentRow>("/v1/documents/files", data);
    },
    onSuccess: () => {
      setForm((prev) => ({ ...prev, url: "" }));
      setFile(null);
      setFileInputKey((prev) => prev + 1);
      void client.invalidateQueries({ queryKey: ["documents"] });
      void client.invalidateQueries({ queryKey: COMM_LOGS_QUERY_KEY });
    },
  });
  const message = fieldMessage(attach.error);

  return (
    <div className="mt-2 rounded border border-gray-100 bg-gray-50 p-3">
      <ul className="flex flex-col gap-1 text-sm">
        {(attachments.data?.items ?? []).map((doc) => (
          <li key={doc.id} className="flex flex-wrap items-center gap-2">
            <span className="cell-nowrap text-gray-600">{doc.document_type_name}</span>
            {doc.storage_kind === "FILE" ? (
              <a href={`/api/v1/documents/${doc.id}/download`} className="underline">
                {orEmpty(doc.original_filename)}
              </a>
            ) : (
              <a href={doc.url ?? "#"} className="underline" rel="noreferrer noopener">
                링크 열기
              </a>
            )}
          </li>
        ))}
        {attachments.data?.items.length === 0 && (
          <li className="text-gray-500">첨부가 없습니다.</li>
        )}
      </ul>
      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-2"
          aria-label={`통신 기록 #${log.id} 첨부`}
          onSubmit={(event) => {
            event.preventDefault();
            attach.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-xs">
            <span className="cell-nowrap text-gray-600">형태</span>
            <select
              value={form.mode}
              onChange={(event) => setForm((prev) => ({ ...prev, mode: event.target.value }))}
              className="rounded border border-gray-300 px-2 py-1"
            >
              <option value="LINK">링크</option>
              <option value="FILE">파일</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="cell-nowrap text-gray-600">서류 종류</span>
            <select
              name="document_type"
              required
              value={form.document_type}
              onChange={(event) => setForm((prev) => ({ ...prev, document_type: event.target.value }))}
              className="rounded border border-gray-300 px-2 py-1"
            >
              <option value="">선택</option>
              {types.data?.items.map((type) => (
                <option key={type.id} value={type.code}>
                  {type.name_ko}
                </option>
              ))}
            </select>
          </label>
          {form.mode === "LINK" ? (
            <label className="flex flex-col gap-1 text-xs">
              <span className="cell-nowrap text-gray-600">주소(https://…)</span>
              <input
                name="url"
                required
                value={form.url}
                onChange={(event) => setForm((prev) => ({ ...prev, url: event.target.value }))}
                className="w-64 rounded border border-gray-300 px-2 py-1"
              />
            </label>
          ) : (
            <label className="flex flex-col gap-1 text-xs">
              <span className="cell-nowrap text-gray-600">파일 (최대 20MiB)</span>
              <input
                key={fileInputKey}
                name="file"
                type="file"
                required
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
          )}
          <button
            type="submit"
            disabled={attach.isPending}
            className="rounded bg-gray-900 px-3 py-1 text-xs text-white disabled:opacity-50"
          >
            첨부
          </button>
          {message && (
            <p role="alert" className="w-full text-xs text-signal-red">
              {message}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

/**
 * 통신 기록 패널 — 이 인증에 대해 대행사·기관과 오간 일의 요지·첨부·다음 액션·기한.
 * 다음 액션 기한이 되면 알림센터에 독촉이 오르고, 새 기록·완료 처리는 정체 시계를 되돌린다.
 */
export function CommLogsPanel({ row, canEdit }: { row: Certification; canEdit: boolean }) {
  const client = useQueryClient();
  const logs = usePagedQuery<CommLog>(
    [...COMM_LOGS_QUERY_KEY, "certification", row.id],
    `/v1/comm-logs?subject_type=CERTIFICATION&subject_id=${row.id}`,
    true,
    FRESH_EVERY_TIME,
  );
  const partners = useAgencyOptions();
  const blank = {
    occurred_on: todayKst(),
    partner_id: row.agency_partner_id === null ? "" : String(row.agency_partner_id),
    summary: "",
    next_action: "",
    next_action_due: "",
  };
  const [form, setForm] = useState(blank);
  const [openAttachments, setOpenAttachments] = useState<number | null>(null);

  const register = useMutation({
    mutationFn: () => {
      // 기한은 다음 액션이 있을 때만 보낸다 — 주인 없는 날짜는 서버가 422로 거절한다.
      const nextAction = orNull(form.next_action);
      const due = nextAction === null ? null : orNull(form.next_action_due);
      return apiFetch<CommLog>("/v1/comm-logs", {
        method: "POST",
        body: {
          subject_type: "CERTIFICATION",
          subject_id: row.id,
          partner_id: form.partner_id === "" ? undefined : Number(form.partner_id),
          occurred_on: form.occurred_on,
          summary: form.summary,
          next_action: nextAction ?? undefined,
          next_action_due: due ?? undefined,
        },
      });
    },
    onSuccess: () => {
      setForm({ ...blank, occurred_on: todayKst() });
      void client.invalidateQueries({ queryKey: COMM_LOGS_QUERY_KEY });
    },
  });
  const complete = useMutation({
    mutationFn: (input: { log: CommLog; doneOn: string | null }) =>
      apiFetch<CommLog>(`/v1/comm-logs/${input.log.id}`, {
        method: "PATCH",
        body: { version: input.log.version, next_action_done_on: input.doneOn },
      }),
    onSuccess: () => void client.invalidateQueries({ queryKey: COMM_LOGS_QUERY_KEY }),
  });
  const remove = useMutation({
    mutationFn: (log: CommLog) => apiDelete(`/v1/comm-logs/${log.id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: COMM_LOGS_QUERY_KEY }),
  });
  const message =
    fieldMessage(register.error) ?? fieldMessage(complete.error) ?? fieldMessage(remove.error);

  return (
    <div className="mt-4 rounded-lg border border-gray-200 p-4">
      <h3 className="font-semibold">통신 기록</h3>
      <p className="mt-1 text-sm text-gray-500">
        대행사·기관과 오간 일의 요지와 다음 액션입니다. 기록만 남기며 발송은 하지 않습니다. 다음 액션
        기한이 되면 알림센터에 독촉이 오릅니다.
      </p>

      {canEdit && (
        <form
          className="mt-3 flex flex-wrap items-end gap-3"
          aria-label="통신 기록 등록"
          onSubmit={(event) => {
            event.preventDefault();
            register.mutate();
          }}
        >
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">오간 날</span>
            <input
              name="occurred_on"
              type="date"
              required
              max={todayKst()}
              value={form.occurred_on}
              onChange={(event) => setForm((prev) => ({ ...prev, occurred_on: event.target.value }))}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">상대 거래처 (선택)</span>
            <select
              name="partner_id"
              value={form.partner_id}
              onChange={(event) => setForm((prev) => ({ ...prev, partner_id: event.target.value }))}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">없음</option>
              {partners.data?.items
                .filter((partner) => partner.type_codes.includes("CERT_AGENCY"))
                .map((partner) => (
                  <option key={partner.id} value={partner.id}>
                    {partner.name_ko}
                  </option>
                ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">요지</span>
            <input
              name="summary"
              required
              maxLength={2000}
              value={form.summary}
              onChange={(event) => setForm((prev) => ({ ...prev, summary: event.target.value }))}
              className="w-72 rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">다음 액션 (선택)</span>
            <input
              name="next_action"
              maxLength={500}
              value={form.next_action}
              onChange={(event) => setForm((prev) => ({ ...prev, next_action: event.target.value }))}
              className="w-56 rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="cell-nowrap text-gray-600">기한</span>
            <input
              name="next_action_due"
              type="date"
              disabled={form.next_action.trim() === ""}
              min={form.occurred_on}
              value={form.next_action_due}
              onChange={(event) =>
                setForm((prev) => ({ ...prev, next_action_due: event.target.value }))
              }
              className="rounded border border-gray-300 px-3 py-2 disabled:bg-gray-100"
            />
          </label>
          <button
            type="submit"
            disabled={register.isPending}
            className="rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            기록
          </button>
        </form>
      )}
      {message && (
        <p role="alert" className="mt-3 text-sm text-signal-red">
          {message}
        </p>
      )}

      {logs.isPending && <p className="mt-3 text-sm text-gray-500">불러오는 중…</p>}
      {logs.data?.items.length === 0 && (
        <p className="mt-3 text-sm text-gray-500">통신 기록이 없습니다.</p>
      )}
      <ul className="mt-3 flex flex-col gap-3">
        {(logs.data?.items ?? []).map((log) => (
          <li key={log.id} className="rounded border border-gray-100 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="cell-nowrap font-medium">{log.occurred_on}</span>
              {log.partner_name !== null && (
                <span className="cell-nowrap text-gray-600">{log.partner_name}</span>
              )}
              <span className="cell-nowrap text-xs text-gray-400">
                입력 {toKstDisplay(log.created_at)}
              </span>
              {followUpBadge(log)}
              <span className="ml-auto flex gap-3">
                <button
                  type="button"
                  className="cell-nowrap text-xs text-gray-700 underline"
                  onClick={() => setOpenAttachments((prev) => (prev === log.id ? null : log.id))}
                >
                  첨부 {log.attachment_count}건{openAttachments === log.id ? " 닫기" : " 보기"}
                </button>
                {canEdit && log.next_action !== null && log.next_action_done_on === null && (
                  <button
                    type="button"
                    className="cell-nowrap text-xs text-gray-700 underline"
                    onClick={() => complete.mutate({ log, doneOn: todayKst() })}
                  >
                    완료 처리
                  </button>
                )}
                {canEdit && log.next_action_done_on !== null && (
                  <button
                    type="button"
                    className="cell-nowrap text-xs text-gray-700 underline"
                    onClick={() => complete.mutate({ log, doneOn: null })}
                  >
                    재개
                  </button>
                )}
                {canEdit && (
                  <button
                    type="button"
                    className="cell-nowrap text-xs text-signal-red underline"
                    onClick={() => {
                      if (window.confirm("이 통신 기록을 삭제할까요? 첨부가 있으면 첨부를 먼저 삭제해야 합니다.")) {
                        remove.mutate(log);
                      }
                    }}
                  >
                    삭제
                  </button>
                )}
              </span>
            </div>
            <p className="mt-1 whitespace-pre-wrap break-keep">{log.summary}</p>
            {log.next_action !== null && (
              <p className="mt-1 text-gray-700">
                다음 액션: {log.next_action}
                {log.next_action_due !== null && ` (기한 ${log.next_action_due})`}
              </p>
            )}
            {openAttachments === log.id && <LogAttachments log={log} canEdit={canEdit} />}
          </li>
        ))}
      </ul>
    </div>
  );
}


/** 실물 유실·손상 목록(409)을 사람이 읽는 문장으로 — 문서 id와 사유만 안다(서버 detail). */
function unavailableText(error: ApiError): string {
  const docs = error.detail.documents;
  if (!Array.isArray(docs) || docs.length === 0) return error.message;
  const parts = (docs as { document_id: number; reason: string }[]).map(
    (doc) => `문서 #${doc.document_id}(${doc.reason === "MISSING" ? "파일 없음" : "내용 불일치"})`,
  );
  return `${error.message} — ${parts.join(", ")}`;
}

/**
 * 전달 서류 zip — 인증 소유 문서+태스크가 서류로 연결한 문서를 한 묶음으로 내려받는다.
 * ★ 생성까지가 시스템이고 발송은 사람이다(§5.4). 통신 기록 첨부는 들어가지 않는다.
 * ★ 실물이 없거나 손상된 서류가 있으면 서버가 조용히 빼지 않고 거부한다(409) — 그 문장을 그대로 보인다.
 */
export function PackagePanel({ row }: { row: Certification }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await apiDownload(
        `/v1/certifications/${row.id}/package`,
        `인증${row.id}_전달서류.zip`,
      );
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof ApiError ? unavailableText(caught) : "내려받지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-4 rounded border p-3">
      <h3 className="font-medium">전달 서류 묶음</h3>
      <p className="mt-1 break-keep text-sm text-gray-600">
        이 인증의 서류(인증 소유 문서+태스크에 연결된 문서)를 zip 한 파일로 받습니다. 보내는 일은 사람이
        합니다.
      </p>
      <button
        type="button"
        className="cell-nowrap mt-2 rounded bg-gray-800 px-3 py-1 text-sm text-white disabled:opacity-50"
        disabled={busy}
        onClick={() => void download()}
      >
        {busy ? "만드는 중…" : "전달 서류 zip 받기"}
      </button>
      {error !== null && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {error}
        </p>
      )}
    </div>
  );
}
