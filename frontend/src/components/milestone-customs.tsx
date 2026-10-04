// 선적 통관 기록 — 목록·추가·정정·삭제 (S3-2 PR-4b — design-D D7 '통관 기록 섹션' / S16~S19 / PROGRESS 'S3-2 PR-4a' 인계 계약).
//
// ★ 통관은 사실 기록이다 — 세율·세액·HS 입력란 없음(§15 법적 판정 금지). 수리일 = 신고수리 실적·적재기한(+30)의 유일 원천(X-02).
// ★ 버튼은 서버 allowed_actions의 EDIT_CUSTOMS일 때만. 신고 구분은 선적 구분에서 고정(수출선적 → 수출신고만 — KIND_MISMATCH 사전 차단).
// ★ 신고번호·신고일 변경과 **기존** 수리일 변경·지우기는 사유 필수(빈칸 제출 불가), 수리일 첫 입력은 기록이라 사유 없음(서버 규칙 그대로).
// ★ 신고일·수리일 상한 = 서버 KST 오늘(보드 `today_kst` — 브라우저 시간대 의존 0), 날짜는 'YYYY-MM-DD' 문자열 비교만.
// ★ 추가 = 멱등 키(대화상자 1회 = 키 1개), 정정·삭제 = version(409 → '최신 내용 불러오기'). 잠금 해제는 요청 finally, 처리 중 Esc 무시.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { ConfirmDialog } from "./confirm-dialog";
import { ListPager } from "./list-pager";
import { ListState } from "./list-state";
import { DialogShell, writeErrorText } from "./milestone-dialogs";
import { SearchSelect } from "./search-select";
import { apiDelete, apiFetch } from "../lib/api";
import { isVersionConflict } from "../lib/api-errors";
import {
  DATE_MIN,
  DECLARATION_NO_MAX,
  DECLARATION_NO_PATTERN,
  customsRecordsKey,
  declarationKindLabel,
  type CustomsRecord,
} from "../lib/milestone";
import { usePagedList } from "../lib/paging";
import { can, createKeyKeeper, type ShipmentDetail } from "../lib/shipment";

const NOUN = "통관 기록";
const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";
const DATE_SHAPE = /^\d{4}-\d{2}-\d{2}$/;
/** 기록 가능한 신고 구분 — 선적 구분과 같아야 한다(채널입고·샘플은 생성 경로가 없다). */
const DECLARABLE_KINDS = new Set(["EXPORT", "IMPORT"]);

interface BrokerChoice {
  id: number;
  name_ko: string;
}

export const CUSTOMS_SECTION_ID = "shipment-customs";
export const CUSTOMS_HEADING_ID = "shipment-customs-title";

/** 신고번호 입력 문제(서버 규칙과 같은 사전 검사 — 서버가 최종). */
export function declarationNoProblem(raw: string): string | null {
  const value = raw.trim();
  if (value === "") return "신고번호를 입력해 주세요.";
  if (value.length > DECLARATION_NO_MAX || !DECLARATION_NO_PATTERN.test(value)) {
    return `신고번호는 영문·숫자·하이픈(-)·슬래시(/)로 ${DECLARATION_NO_MAX}자 이내여야 합니다(첫 글자는 영문·숫자, 공백 없음).`;
  }
  return null;
}

/** 신고일·수리일 문제 — 형식·범위(2000-01-01 ~ 서버 KST 오늘)·수리일 ≥ 신고일(문자열 비교). */
export function customsDateProblem(declaredOn: string, acceptedOn: string, todayKst: string): string | null {
  const inRange = (value: string) => DATE_SHAPE.test(value) && value >= DATE_MIN && value <= todayKst;
  if (!inRange(declaredOn)) return `신고일은 ${DATE_MIN} ~ ${todayKst}(오늘) 사이로 입력해 주세요.`;
  if (acceptedOn !== "" && !inRange(acceptedOn)) return `수리일은 ${DATE_MIN} ~ ${todayKst}(오늘) 사이로 입력해 주세요(미수리면 비워 두세요).`;
  if (acceptedOn !== "" && acceptedOn < declaredOn) return "수리일이 신고일보다 앞설 수 없습니다.";
  return null;
}

export function CustomsSection({ shipment, onChanged }: { shipment: ShipmentDetail; onChanged: () => void }) {
  const client = useQueryClient();
  const canEdit = can(shipment, "EDIT_CUSTOMS");
  const declarable = DECLARABLE_KINDS.has(shipment.shipment_kind);
  const todayKst = shipment.milestones.today_kst;
  const listKey = customsRecordsKey(shipment.id);
  const list = usePagedList<CustomsRecord>(listKey, `/v1/shipments/${shipment.id}/customs-records`, true, { staleTime: 0 });
  const [dialog, setDialog] = useState<{ record: CustomsRecord | null } | null>(null);
  const [removeTarget, setRemoveTarget] = useState<CustomsRecord | null>(null);
  const removeLock = useRef(false);
  const summary = shipment.customs_summary;

  function refreshAll() {
    void client.invalidateQueries({ queryKey: listKey });
    onChanged();
  }

  const remove = useMutation({
    mutationFn: async (input: { record: CustomsRecord; reason: string }) => {
      try {
        await apiDelete(`/v1/shipments/${shipment.id}/customs-records/${input.record.id}`, {
          body: { version: input.record.version, reason: input.reason },
        });
      } finally {
        removeLock.current = false;
      }
    },
    onSuccess: () => {
      setRemoveTarget(null);
      refreshAll();
    },
  });

  return (
    <section id={CUSTOMS_SECTION_ID} aria-labelledby={CUSTOMS_HEADING_ID}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id={CUSTOMS_HEADING_ID} tabIndex={-1} className="text-lg font-semibold">
          통관 기록
        </h2>
        {canEdit && declarable && (
          <button
            type="button"
            onClick={() => setDialog({ record: null })}
            className="cell-nowrap rounded border border-gray-900 px-3 py-2 text-sm"
          >
            통관 기록 추가
          </button>
        )}
      </div>
      <p className="mt-1 break-keep text-xs text-gray-500">
        세율·세액·HS 판정은 기록하지 않습니다 — 관세사 신고 결과를 사실로만 남깁니다. 수리일은 신고수리 실적(가장 이른 수리일)
        {shipment.shipment_kind === "EXPORT" && "과 적재기한(수리일+30일)"}의 근거입니다.
      </p>
      <p className="mt-1 text-xs text-gray-600">
        <span className="cell-nowrap">살아 있는 기록 {summary.live_count}건</span> ·{" "}
        <span className="cell-nowrap">미수리 {summary.pending_count}건</span> ·{" "}
        <span className="cell-nowrap">최근 수리일 {summary.latest_accepted_on ?? "—"}</span>
      </p>
      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-2" />
      <div className="mt-2 overflow-x-auto rounded-lg border border-gray-200">
        <ListState
          isPending={list.isPending}
          error={list.error}
          isEmpty={list.data?.items.length === 0}
          emptyHint={canEdit ? "통관 기록이 없습니다. 관세사 신고 결과가 나오면 '통관 기록 추가'로 남기세요." : "통관 기록이 없습니다."}
        >
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">구분</th>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">신고번호</th>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">신고일</th>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">수리일</th>
                <th scope="col" className="cell-nowrap px-3 py-2">관세사</th>
                <th scope="col" className="cell-nowrap min-w-40 px-3 py-2">메모</th>
                {canEdit && <th scope="col" className="cell-nowrap px-3 py-2 text-center">편집</th>}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((record) => (
                <tr key={record.id} className="border-t border-gray-100 align-top">
                  <td className="cell-nowrap px-3 py-2 text-center">{declarationKindLabel(record.declaration_kind)}</td>
                  <td className="num cell-nowrap px-3 py-2">{record.declaration_no}</td>
                  <td className="num cell-nowrap px-3 py-2">{record.declared_on}</td>
                  <td className="num cell-nowrap px-3 py-2">
                    {record.accepted_on ?? (
                      <span className="rounded border border-signal-amber bg-signal-amber/20 px-1.5 py-0.5 text-xs">미수리</span>
                    )}
                  </td>
                  <td className="cell-nowrap px-3 py-2">{record.customs_broker?.name ?? "—"}</td>
                  <td className="whitespace-pre-line break-keep px-3 py-2">{record.note ?? "—"}</td>
                  {canEdit && (
                    <td className="cell-nowrap px-3 py-2 text-center">
                      <button type="button" onClick={() => setDialog({ record })} className="underline">
                        정정
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          remove.reset();
                          setRemoveTarget(record);
                        }}
                        className="ml-3 text-gray-500 underline"
                      >
                        삭제
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </ListState>
      </div>

      {dialog !== null && canEdit && (
        <CustomsRecordDialog
          shipment={shipment}
          record={dialog.record}
          todayKst={todayKst}
          onSaved={() => {
            setDialog(null);
            refreshAll();
          }}
          onClose={() => setDialog(null)}
          onReload={() => {
            setDialog(null);
            refreshAll();
          }}
        />
      )}
      {removeTarget !== null && canEdit && (
        <ConfirmDialog
          title="통관 기록을 삭제할까요?"
          danger
          confirmLabel="삭제"
          reasonLabel="삭제 사유 (필수)"
          description={
            <p>
              {declarationKindLabel(removeTarget.declaration_kind)} {removeTarget.declaration_no}을(를) 삭제합니다. 삭제 사실과 사유는 감사
              기록에 남습니다. 수리일이 있었다면 신고수리 실적·적재기한이 다시 계산됩니다.
            </p>
          }
          pending={remove.isPending}
          error={remove.error ? writeErrorText(remove.error, NOUN) : null}
          onReload={
            isVersionConflict(remove.error)
              ? () => {
                  remove.reset();
                  setRemoveTarget(null);
                  refreshAll();
                }
              : undefined
          }
          onCancel={() => {
            remove.reset();
            setRemoveTarget(null);
          }}
          onConfirm={(reason) => {
            if (removeLock.current) return;
            removeLock.current = true;
            remove.mutate({ record: removeTarget, reason });
          }}
        />
      )}
    </section>
  );
}

function CustomsRecordDialog({
  shipment,
  record,
  todayKst,
  onSaved,
  onClose,
  onReload,
}: {
  shipment: ShipmentDetail;
  /** null = 추가, 값 = 정정(연 순간의 version 고정). */
  record: CustomsRecord | null;
  todayKst: string;
  onSaved: () => void;
  onClose: () => void;
  onReload: () => void;
}) {
  const titleRef = useRef<HTMLHeadingElement | null>(null);
  const firstRef = useRef<HTMLInputElement | null>(null);
  const numberId = useId();
  const declaredId = useId();
  const acceptedId = useId();
  const noteId = useId();
  const reasonId = useId();
  const kind = record?.declaration_kind ?? shipment.shipment_kind;
  const initialBroker: BrokerChoice | null =
    record?.customs_broker == null ? null : { id: record.customs_broker.partner_id, name_ko: record.customs_broker.name };

  const [number, setNumber] = useState(record?.declaration_no ?? "");
  const [declaredOn, setDeclaredOn] = useState(record?.declared_on ?? "");
  const [acceptedOn, setAcceptedOn] = useState(record?.accepted_on ?? "");
  const [broker, setBroker] = useState<BrokerChoice | null>(initialBroker);
  const [note, setNote] = useState(record?.note ?? "");
  const [reason, setReason] = useState("");
  const [keys] = useState(() => createKeyKeeper());
  const lock = useRef(false);

  // ── 바뀐 필드(정정) ──
  const changes: Record<string, unknown> = {};
  if (record !== null) {
    if (number.trim().toUpperCase() !== record.declaration_no) changes.declaration_no = number.trim();
    if (declaredOn !== record.declared_on) changes.declared_on = declaredOn;
    if ((acceptedOn === "" ? null : acceptedOn) !== record.accepted_on) changes.accepted_on = acceptedOn === "" ? null : acceptedOn;
    if ((broker?.id ?? null) !== (record.customs_broker?.partner_id ?? null)) changes.customs_broker_partner_id = broker?.id ?? null;
    if ((note.trim() === "" ? null : note.trim()) !== record.note) changes.note = note.trim() === "" ? null : note.trim();
  }
  const reasonRequired =
    record !== null &&
    ("declaration_no" in changes || "declared_on" in changes || ("accepted_on" in changes && record.accepted_on !== null));
  const acceptedChanged = record === null ? acceptedOn !== "" : "accepted_on" in changes;

  const numberProblem = declarationNoProblem(number);
  const dateProblem = customsDateProblem(declaredOn, acceptedOn, todayKst);
  const nothingChanged = record !== null && Object.keys(changes).length === 0;
  const reasonMissing = reasonRequired && reason.trim() === "";

  const save = useMutation({
    mutationFn: async (input: { key: string | null; body: Record<string, unknown> }) => {
      try {
        if (record === null) {
          return await apiFetch<CustomsRecord>(`/v1/shipments/${shipment.id}/customs-records`, {
            method: "POST",
            idempotencyKey: input.key ?? undefined,
            body: input.body,
          });
        }
        return await apiFetch<CustomsRecord>(`/v1/shipments/${shipment.id}/customs-records/${record.id}`, {
          method: "PATCH",
          body: input.body,
        });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: onSaved,
  });

  const blocked = save.isPending || numberProblem !== null || dateProblem !== null || nothingChanged || reasonMissing;

  function submit() {
    if (blocked || lock.current) return;
    lock.current = true;
    if (record === null) {
      const body: Record<string, unknown> = {
        declaration_kind: kind,
        declaration_no: number.trim(),
        declared_on: declaredOn,
        ...(acceptedOn === "" ? {} : { accepted_on: acceptedOn }),
        ...(broker === null ? {} : { customs_broker_partner_id: broker.id }),
        ...(note.trim() === "" ? {} : { note: note.trim() }),
      };
      save.mutate({ key: keys.keyFor(JSON.stringify(body)), body });
    } else {
      const body = { version: record.version, ...changes, ...(reasonRequired ? { reason: reason.trim() } : {}) };
      save.mutate({ key: null, body });
    }
  }

  const touched = number !== (record?.declaration_no ?? "") || declaredOn !== (record?.declared_on ?? "");

  return (
    <DialogShell
      title={record === null ? `통관 기록 추가 — ${declarationKindLabel(kind)}` : `통관 기록 정정 — ${record.declaration_no}`}
      titleRef={titleRef}
      pending={save.isPending}
      onClose={onClose}
      initialFocus={firstRef}
    >
      <form
        className="mt-3 flex flex-col gap-3 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <p className="break-keep text-xs text-gray-500">
          신고 구분: <strong>{declarationKindLabel(kind)}</strong>(선적 구분에서 고정). 세율·세액·HS는 기록하지 않습니다.
        </p>
        <fieldset disabled={save.isPending} className="contents">
          <div className="flex flex-col gap-1">
            <label htmlFor={numberId} className="cell-nowrap text-gray-600">
              신고번호 (필수)
            </label>
            <input
              id={numberId}
              ref={firstRef}
              value={number}
              maxLength={DECLARATION_NO_MAX}
              aria-required="true"
              aria-invalid={touched && numberProblem !== null}
              onChange={(event) => setNumber(event.target.value)}
              className={`${inputClass} w-64`}
            />
            <span className="break-keep text-xs text-gray-500">영문·숫자·-·/ — 서버가 대문자로 저장합니다.</span>
          </div>
          <div className="flex flex-wrap gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor={declaredId} className="cell-nowrap text-gray-600">
                신고일 (필수)
              </label>
              <input
                id={declaredId}
                type="date"
                min={DATE_MIN}
                max={todayKst}
                value={declaredOn}
                aria-required="true"
                onChange={(event) => setDeclaredOn(event.target.value)}
                className={`${inputClass} w-44`}
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor={acceptedId} className="cell-nowrap text-gray-600">
                수리일 (미수리면 비움)
              </label>
              <input
                id={acceptedId}
                type="date"
                min={DATE_MIN}
                max={todayKst}
                value={acceptedOn}
                onChange={(event) => setAcceptedOn(event.target.value)}
                className={`${inputClass} w-44`}
              />
            </div>
          </div>
          {acceptedChanged && shipment.shipment_kind === "EXPORT" && (
            <p role="note" className="break-keep text-xs text-gray-600">
              수리일이 바뀌면 신고수리 실적과 적재기한(수리일+30일)이 다시 계산됩니다.
            </p>
          )}
          <div className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">관세사 (선택)</span>
            <SearchSelect<BrokerChoice>
              label="관세사 거래처"
              path="/v1/partners"
              params={{ type: "CUSTOMS_BROKER" }}
              queryKey={["partners"]}
              value={broker}
              onChange={setBroker}
              getKey={(item) => item.id}
              getLabel={(item) => item.name_ko}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label htmlFor={noteId} className="cell-nowrap text-gray-600">
              메모 (선택)
            </label>
            <textarea
              id={noteId}
              rows={2}
              maxLength={1000}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className={inputClass}
            />
          </div>
          {reasonRequired && (
            <div className="flex flex-col gap-1">
              <label htmlFor={reasonId} className="cell-nowrap text-gray-600">
                정정 사유 (필수)
              </label>
              <textarea
                id={reasonId}
                rows={2}
                maxLength={500}
                aria-required="true"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                className={inputClass}
              />
              <span className="break-keep text-xs text-gray-500">
                신고번호·신고일 변경과 기존 수리일 변경·지우기는 사유와 함께 감사 기록에 남습니다.
              </span>
            </div>
          )}
        </fieldset>

        {touched && numberProblem !== null && (
          <p role="alert" className="break-keep text-xs text-signal-red">
            {numberProblem}
          </p>
        )}
        {declaredOn !== "" && dateProblem !== null && (
          <p role="alert" className="break-keep text-xs text-signal-red">
            {dateProblem}
          </p>
        )}
        {nothingChanged && <p className="text-xs text-gray-500">바뀐 내용이 없습니다.</p>}
        {save.error && (
          <div role="alert" className="break-keep text-sm text-signal-red">
            <p>{writeErrorText(save.error, NOUN)}</p>
            {isVersionConflict(save.error) && (
              <button
                type="button"
                onClick={onReload}
                className="cell-nowrap mt-2 rounded border border-gray-300 px-3 py-1 text-sm text-gray-900"
              >
                최신 내용 불러오기
              </button>
            )}
          </div>
        )}
        <div className="mt-2 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={save.isPending}
            className="cell-nowrap rounded border border-gray-300 px-4 py-2 text-sm disabled:opacity-50"
          >
            닫기
          </button>
          <button
            type="submit"
            disabled={blocked}
            className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {save.isPending ? "처리 중…" : record === null ? "추가" : "정정 저장"}
          </button>
        </div>
      </form>
    </DialogShell>
  );
}
