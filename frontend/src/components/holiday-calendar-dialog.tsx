// 휴일 캘린더 편집 대화상자 (S3-2 PR-2b — design-D §D2-3 H2·H3·H5 / 서버 계약: PROGRESS 'S3-2 PR-2a' PR-2b 인계 계약). ADMIN 전용.
//
// ★ H3은 그 국가·연도의 **전체 집합** 원자 교체다 — 편집본은 화면 1쪽이 아니라 전체(`fetchFullCalendar`)에서 시작한다.
// ★ 멱등 키: 대화상자를 한 번 열 때 1개. 같은 본문 재시도는 같은 키, 본문(근거·확인일·행·CSV 채우기)이 바뀌면 새 키.
// ★ version: 연 시점에 읽은 `calendar.version`(미선언이면 null) — 낡으면 서버 409 → '최신 내용 불러오기'.
// ★ CSV는 미리보기(H5, 비저장) → 문제 0이면 편집본을 채우고, 문제가 1건이라도 있으면 저장을 막는다(파일 전체 원자).
// ★ 날짜는 문자열 그대로('YYYY-MM-DD') — Date로 바꾸지 않는다. 비교는 문자열(같은 자릿수 ISO 날짜는 사전순 = 날짜순).
// 규약: 한국어 break-keep · 좁은 칸 nowrap · 숫자·날짜 가운데 정렬 · 390px에서 행은 줄바꿈해 쌓인다.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { ApiError, apiFetch, apiUpload } from "../lib/api";
import { todayKst } from "../lib/datetime";
import {
  HOLIDAYS_QUERY_KEY,
  HOLIDAY_NAME_MAX,
  MAX_HOLIDAYS_PER_YEAR,
  SOURCE_URL_MAX,
  fetchFullCalendar,
  mapReplaceError,
  precheckHolidayCsv,
  precheckRows,
  previewPath,
  problemFieldLabel,
  replacePath,
  type CalendarYear,
  type CsvPreview,
  type CsvPreviewProblem,
  type Holiday,
  type ReplaceBody,
} from "../lib/holidays";
import { useDialogBehavior } from "./confirm-dialog";
import { TruncationNotice } from "./truncation-notice";

/** CSV 문제 목록 표시 상한 — 넘으면 잘림을 고지한다(조용한 잘림 금지). */
const PROBLEMS_SHOWN = 100;

interface Row {
  uid: number;
  holiday_on: string;
  name: string;
}

interface FieldErrors {
  summary: string | null;
  sourceUrl: string | null;
  verifiedOn: string | null;
  rowDate: Record<number, string>;
  rowName: Record<number, string>;
  conflict: boolean;
}

const NO_ERRORS: FieldErrors = { summary: null, sourceUrl: null, verifiedOn: null, rowDate: {}, rowName: {}, conflict: false };

interface DialogProps {
  country: string;
  year: string;
  onClose: () => void;
  onSaved: (calendar: CalendarYear) => void;
}

export function HolidayCalendarDialog({ country, year, onClose, onSaved }: DialogProps) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const titleId = useId();
  useDialogBehavior(boxRef, onClose);
  // 편집 시작점 = 전체 집합(쪽 합치기 + 건수·version 대조). 열려 있는 동안 다시 읽지 않는다(입력 중 편집본을 덮지 않게).
  const full = useQuery({
    queryKey: [...HOLIDAYS_QUERY_KEY, "edit", country, year],
    queryFn: () => fetchFullCalendar(country, year),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4">
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-5 shadow-lg"
      >
        <h2 id={titleId} className="text-lg font-bold">
          휴일 캘린더 편집 <span className="cell-nowrap">({country} {year})</span>
        </h2>
        {full.isPending && <p className="mt-3 text-sm text-gray-500">편집할 휴일 목록을 불러오는 중…</p>}
        {full.error && (
          <div role="alert" className="mt-3 break-keep text-sm text-signal-red">
            <p>{full.error instanceof Error ? full.error.message : "휴일 목록을 불러오지 못했습니다."}</p>
            <button type="button" onClick={onClose} className="cell-nowrap mt-3 rounded border border-gray-300 px-4 py-2 text-sm">
              닫기
            </button>
          </div>
        )}
        {full.data && (
          <EditorForm
            country={country}
            year={year}
            calendar={full.data.calendar}
            holidays={full.data.holidays}
            onClose={onClose}
            onSaved={onSaved}
          />
        )}
      </div>
    </div>
  );
}

interface EditorProps {
  country: string;
  year: string;
  calendar: CalendarYear | null;
  holidays: Holiday[];
  onClose: () => void;
  onSaved: (calendar: CalendarYear) => void;
}

function EditorForm({ country, year, calendar, holidays, onClose, onSaved }: EditorProps) {
  const client = useQueryClient();
  const ids = {
    source: useId(),
    sourceError: useId(),
    verified: useId(),
    verifiedError: useId(),
    csv: useId(),
    csvError: useId(),
    status: useId(),
  };
  const uidSeq = useRef(0);
  const toRow = (item: { holiday_on: string; name: string }): Row => ({
    uid: ++uidSeq.current,
    holiday_on: item.holiday_on,
    name: item.name,
  });
  // 연 시점의 값을 고정한다 — 목록이 재조회돼도 편집본을 조용히 덮지 않는다(충돌은 서버 409가 알린다).
  const [rows, setRows] = useState<Row[]>(() => holidays.map(toRow));
  const [sourceUrl, setSourceUrl] = useState(calendar?.source_url ?? "");
  const [verifiedOn, setVerifiedOn] = useState(calendar?.verified_on ?? "");
  const baseVersion = useRef<number | null>(calendar?.version ?? null);
  const baseCount = useRef(holidays.length);
  // 멱등 키: 대화상자를 연 순간 1개(재시도 재사용) — 본문이 바뀌면 새 키.
  const [initialKey] = useState(() => crypto.randomUUID());
  const keyRef = useRef(initialKey);
  const lock = useRef(false);
  const [errors, setErrors] = useState<FieldErrors>(NO_ERRORS);

  // CSV 미리보기 상태 — 문제가 있으면 저장 차단.
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);
  const [csvProblems, setCsvProblems] = useState<{ fileName: string; problems: CsvPreviewProblem[] } | null>(null);
  const [csvFilled, setCsvFilled] = useState<{ fileName: string; count: number } | null>(null);

  const today = todayKst();

  const save = useMutation({
    mutationFn: (input: { body: ReplaceBody; key: string }) =>
      apiFetch<CalendarYear>(replacePath(country, year), { method: "PUT", body: input.body, idempotencyKey: input.key }),
    // 목록 무효화는 훅 옵션에서 — 관찰자(대화상자)가 사라져도 실행된다.
    onSuccess: () => client.invalidateQueries({ queryKey: HOLIDAYS_QUERY_KEY }),
  });

  const preview = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file, file.name);
      return apiUpload<CsvPreview>(previewPath(country, year), form);
    },
  });

  /** 본문이 바뀌었다 — 새 키. 서버 오류 표시는 다음 저장 시도까지 남겨 둔다(어느 칸을 고칠지 보이게). */
  function bodyChanged() {
    keyRef.current = crypto.randomUUID();
  }

  function updateRow(uid: number, patch: Partial<Omit<Row, "uid">>) {
    bodyChanged();
    setRows((prev) => prev.map((row) => (row.uid === uid ? { ...row, ...patch } : row)));
  }

  function removeRow(uid: number) {
    bodyChanged();
    setRows((prev) => prev.filter((row) => row.uid !== uid));
  }

  function addRow() {
    bodyChanged();
    setRows((prev) => [...prev, toRow({ holiday_on: "", name: "" })]);
  }

  function clearRows() {
    bodyChanged();
    setRows([]);
  }

  /** 행 순번 기준 문구 → 행 uid 기준(삭제·추가로 순번이 밀려도 칸이 어긋나지 않게). */
  function byUid(sent: Row[], byIndex: Record<number, string>): Record<number, string> {
    const out: Record<number, string> = {};
    for (const [index, message] of Object.entries(byIndex)) {
      const row = sent[Number(index)];
      if (row) out[row.uid] = message;
    }
    return out;
  }

  function submit() {
    if (lock.current || csvProblems !== null) return;
    const url = sourceUrl.trim();
    const next: FieldErrors = { ...NO_ERRORS };
    if (url === "") next.sourceUrl = "근거 링크를 입력해 주세요.";
    else if (!/^https?:\/\//.test(url)) next.sourceUrl = "근거 링크는 http:// 또는 https://로 시작해야 합니다.";
    if (verifiedOn === "") next.verifiedOn = "확인일을 입력해 주세요.";
    else if (verifiedOn > today) next.verifiedOn = "확인일은 오늘(한국 날짜) 또는 그 이전이어야 합니다.";
    const rowProblems = precheckRows(year, rows);
    if (rowProblems) {
      next.rowDate = byUid(rows, rowProblems.rowDate);
      next.rowName = byUid(rows, rowProblems.rowName);
    }
    if (next.sourceUrl || next.verifiedOn || rowProblems) {
      setErrors({ ...next, summary: "표시한 칸을 고친 뒤 다시 저장해 주세요." });
      return;
    }
    const sent = rows;
    const body: ReplaceBody = {
      source_url: url,
      verified_on: verifiedOn,
      holidays: sent.map((row) => ({ holiday_on: row.holiday_on, name: row.name.trim() })),
      version: baseVersion.current,
    };
    setErrors(NO_ERRORS);
    lock.current = true; // 동기 잠금 — isPending은 한 틱 늦게 켜진다
    save.mutate(
      { body, key: keyRef.current },
      {
        onSuccess: (saved) => onSaved(saved),
        onError: (error) => {
          const view = mapReplaceError(error, body.holidays);
          setErrors({
            summary: view.summary,
            sourceUrl: view.sourceUrl,
            verifiedOn: view.verifiedOn,
            rowDate: byUid(sent, view.rowDate),
            rowName: byUid(sent, view.rowName),
            conflict: view.conflict,
          });
        },
        onSettled: () => (lock.current = false),
      },
    );
  }

  function onPickCsv(file: File | null) {
    setCsvFile(file);
    setCsvError(file === null ? null : precheckHolidayCsv(file));
  }

  function runPreview() {
    if (preview.isPending) return;
    if (csvFile === null) {
      setCsvError("미리보기할 CSV 파일을 먼저 골라 주세요.");
      fileRef.current?.focus();
      return;
    }
    const problem = precheckHolidayCsv(csvFile);
    if (problem !== null) {
      setCsvError(problem);
      return;
    }
    setCsvError(null);
    const fileName = csvFile.name;
    preview.mutate(csvFile, {
      onSuccess: (result) => {
        if (result.problems.length > 0) {
          // 파일 전체 원자 — 일부 행만 채우지 않는다. 문제가 남아 있는 동안 저장 불가.
          setCsvProblems({ fileName, problems: result.problems });
          setCsvFilled(null);
          return;
        }
        setCsvProblems(null);
        bodyChanged();
        setRows(result.rows.map(toRow));
        setCsvFilled({ fileName, count: result.rows.length });
        setErrors(NO_ERRORS);
      },
      onError: (error) => {
        setCsvFilled(null);
        if (error instanceof ApiError) {
          const fileDetail = typeof error.detail.file === "string" ? ` (${error.detail.file})` : "";
          setCsvError(`${error.message}${fileDetail}`);
        } else {
          setCsvError("미리보기를 받지 못했습니다. 잠시 후 다시 시도해 주세요.");
        }
      },
    });
  }

  function discardCsv() {
    setCsvProblems(null);
    setCsvFile(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  const blockedByCsv = csvProblems !== null;
  const shownProblems = csvProblems?.problems.slice(0, PROBLEMS_SHOWN) ?? [];

  return (
    <form
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="mt-2 flex flex-col gap-4 text-sm"
    >
      <p className="break-keep text-gray-600">
        저장하면 이 국가·연도의 휴일이 아래 목록 <strong>전체</strong>로 바뀝니다(목록에 없는 기존 휴일은 지워집니다). 휴일이 하나도
        없는 해라면 목록을 비운 채 저장하세요 — '휴일 없음 확인'으로 기록됩니다.
      </p>

      <fieldset className="flex flex-col gap-3">
        <legend className="font-semibold">근거 (필수)</legend>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.source} className="text-gray-600">
            근거 링크
          </label>
          <input
            id={ids.source}
            type="url"
            value={sourceUrl}
            maxLength={SOURCE_URL_MAX}
            aria-required="true"
            aria-invalid={errors.sourceUrl !== null}
            aria-describedby={errors.sourceUrl ? ids.sourceError : undefined}
            placeholder="https://"
            onChange={(event) => {
              bodyChanged();
              setSourceUrl(event.target.value);
            }}
            className="min-w-0 rounded border border-gray-300 px-3 py-2"
          />
          {errors.sourceUrl && (
            <span id={ids.sourceError} className="break-keep text-xs text-signal-red">
              {errors.sourceUrl}
            </span>
          )}
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor={ids.verified} className="break-keep text-gray-600">
            확인일 (근거를 확인한 날, 오늘 이전)
          </label>
          <input
            id={ids.verified}
            type="date"
            value={verifiedOn}
            max={today}
            aria-required="true"
            aria-invalid={errors.verifiedOn !== null}
            aria-describedby={errors.verifiedOn ? ids.verifiedError : undefined}
            onChange={(event) => {
              bodyChanged();
              setVerifiedOn(event.target.value);
            }}
            className="num w-fit rounded border border-gray-300 px-3 py-2"
          />
          {errors.verifiedOn && (
            <span id={ids.verifiedError} className="break-keep text-xs text-signal-red">
              {errors.verifiedOn}
            </span>
          )}
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-2 rounded border border-gray-200 p-3">
        <legend className="px-1 font-semibold">CSV로 채우기 (선택)</legend>
        <p className="break-keep text-xs text-gray-500">
          머리글 <code>holiday_on,name</code>, 날짜는 2026-10-01 형식, 'CSV UTF-8'로 저장한 파일만 받습니다. 미리보기는 저장하지 않으며, 문제가
          없으면 아래 목록을 파일 내용으로 바꿉니다.
        </p>
        <label htmlFor={ids.csv} className="text-gray-600">
          휴일 CSV 파일
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id={ids.csv}
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            aria-invalid={csvError !== null}
            aria-describedby={csvError ? ids.csvError : undefined}
            onChange={(event) => onPickCsv(event.target.files?.[0] ?? null)}
            className="min-w-0 max-w-full text-xs"
          />
          <button
            type="button"
            onClick={runPreview}
            disabled={preview.isPending}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
          >
            {preview.isPending ? "미리보기 중…" : "미리보기"}
          </button>
        </div>
        {csvError && (
          <p id={ids.csvError} role="alert" className="break-keep text-xs text-signal-red">
            {csvError}
          </p>
        )}
        {csvFilled && (
          <p role="status" className="break-keep text-xs text-gray-700">
            {csvFilled.fileName}의 <span className="num">{csvFilled.count}</span>건으로 아래 목록을 채웠습니다. 근거 링크·확인일을 확인한 뒤
            저장하세요.
          </p>
        )}
        {csvProblems && (
          <div role="alert" className="flex flex-col gap-2 rounded border border-signal-red p-2">
            <p className="break-keep">
              {csvProblems.fileName}에 문제가 <span className="num">{csvProblems.problems.length}</span>건 있어 목록을 채우지 않았습니다. 파일을 고쳐
              다시 미리보기하거나 'CSV 결과 버리기'를 눌러야 저장할 수 있습니다.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-600">
                  <tr>
                    <th scope="col" className="cell-nowrap px-2 py-1 text-center">
                      행
                    </th>
                    <th scope="col" className="cell-nowrap px-2 py-1 text-center">
                      칸
                    </th>
                    <th scope="col" className="cell-nowrap px-2 py-1 text-left">
                      문제
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {shownProblems.map((problem, index) => (
                    <tr key={`${problem.row}-${problem.field}-${index}`} className="border-t border-gray-100">
                      <td className="num cell-nowrap px-2 py-1 text-center">{problem.row}</td>
                      <td className="cell-nowrap px-2 py-1 text-center">{problemFieldLabel(problem.field)}</td>
                      <td className="break-keep px-2 py-1">{problem.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <TruncationNotice total={csvProblems.problems.length} shown={shownProblems.length}>
              앞의 문제부터 고친 뒤 다시 미리보기하세요.
            </TruncationNotice>
            <button type="button" onClick={discardCsv} className="cell-nowrap w-fit rounded border border-gray-300 px-3 py-1">
              CSV 결과 버리기
            </button>
          </div>
        )}
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="font-semibold">
          휴일 목록 <span className="num font-normal text-gray-500">({rows.length}건)</span>
        </legend>
        {rows.length === 0 && (
          <p className="break-keep text-gray-500">휴일이 없습니다. 이대로 저장하면 이 연도는 '휴일 없음 확인'으로 기록됩니다.</p>
        )}
        <ol aria-label="편집 중인 휴일" className="flex flex-col gap-2">
          {rows.map((row, index) => {
            const dateError = errors.rowDate[row.uid];
            const nameError = errors.rowName[row.uid];
            const dateErrorId = `${ids.status}-d-${row.uid}`;
            const nameErrorId = `${ids.status}-n-${row.uid}`;
            return (
              <li key={row.uid} className="flex flex-wrap items-start gap-2 border-b border-gray-100 pb-2">
                <div className="flex flex-col gap-1">
                  <input
                    type="date"
                    aria-label={`${index + 1}번째 휴일 날짜`}
                    value={row.holiday_on}
                    min={`${year}-01-01`}
                    max={`${year}-12-31`}
                    aria-invalid={dateError !== undefined}
                    aria-describedby={dateError ? dateErrorId : undefined}
                    onChange={(event) => updateRow(row.uid, { holiday_on: event.target.value })}
                    className="num rounded border border-gray-300 px-2 py-1"
                  />
                  {dateError && (
                    <span id={dateErrorId} className="break-keep text-xs text-signal-red">
                      {dateError}
                    </span>
                  )}
                </div>
                <div className="flex min-w-0 flex-1 basis-40 flex-col gap-1">
                  <input
                    aria-label={`${index + 1}번째 휴일 이름`}
                    value={row.name}
                    maxLength={HOLIDAY_NAME_MAX}
                    aria-invalid={nameError !== undefined}
                    aria-describedby={nameError ? nameErrorId : undefined}
                    onChange={(event) => updateRow(row.uid, { name: event.target.value })}
                    className="w-full rounded border border-gray-300 px-2 py-1"
                  />
                  {nameError && (
                    <span id={nameErrorId} className="break-keep text-xs text-signal-red">
                      {nameError}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  aria-label={`${index + 1}번째 휴일 삭제`}
                  onClick={() => removeRow(row.uid)}
                  className="cell-nowrap px-1 py-1 text-gray-500 underline"
                >
                  삭제
                </button>
              </li>
            );
          })}
        </ol>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={addRow}
            disabled={rows.length >= MAX_HOLIDAYS_PER_YEAR}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1 disabled:opacity-50"
          >
            휴일 추가
          </button>
          {rows.length > 0 && (
            <button type="button" onClick={clearRows} className="cell-nowrap rounded border border-gray-300 px-3 py-1 text-gray-600">
              모두 지우기
            </button>
          )}
        </div>
      </fieldset>

      {errors.summary && (
        <div role="alert" className="break-keep text-signal-red">
          <p>{errors.summary}</p>
          {errors.conflict && (
            <button
              type="button"
              onClick={() => {
                void client.invalidateQueries({ queryKey: HOLIDAYS_QUERY_KEY });
                onClose();
              }}
              className="cell-nowrap mt-2 rounded border border-signal-red px-3 py-1"
            >
              최신 내용 불러오기
            </button>
          )}
        </div>
      )}

      <p id={ids.status} className="break-keep text-xs text-gray-500">
        {baseVersion.current === null
          ? `${country} ${year} 휴일 캘린더를 처음 선언합니다(${rows.length}건).`
          : `기존 ${baseCount.current}건이 ${rows.length}건으로 바뀝니다.`}
        {blockedByCsv && " CSV 문제가 남아 있어 저장할 수 없습니다."}
      </p>

      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} className="cell-nowrap rounded border border-gray-300 px-4 py-2">
          닫기
        </button>
        <button
          type="submit"
          disabled={save.isPending || blockedByCsv}
          aria-describedby={ids.status}
          className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-50"
        >
          {save.isPending ? "저장 중…" : "저장"}
        </button>
      </div>
    </form>
  );
}
