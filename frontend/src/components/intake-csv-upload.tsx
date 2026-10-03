// 오더 인테이크 CSV 업로드 패널 (S3-1 PR-14b — DESIGN §7.4 [M4] PR-14a 부기·§12.1·§12.2 / 서버 계약: PROGRESS 'S3-1 PR-14a' PR-14b 인계 계약).
//
// ★ 파일 전체 원자 — 한 행이라도 오류면 서버가 아무것도 등록하지 않는다. 프런트는 행을 다시 검증하지 않고(사전 검사는 확장자·크기·빈 파일뿐),
//   리포트는 서버 값 그대로(행번호·열·message_ko) 표시한다. 영문 code는 한국어 라벨로만 보인다.
// ★ 멱등 키: '업로드' 클릭마다 새 키. 같은 키·같은 파일은 응답 유실(네트워크·504)의 '결과 다시 받기'와 LOCK_BUSY의 '같은 요청으로 다시 시도'에서만.
//   다른 파일을 고르면 재시도 상태를 버린다(같은 키·다른 파일은 서버가 KEY_CONFLICT로 거절한다).
// ★ 양식·업로드는 무역·관리자만(서버 계약) — 호출부가 역할로 숨기고, 그래도 403이면 래치해 조작을 거둔다.
// 규약: 한국어 break-keep · 좁은 셀 nowrap · 숫자 가운데 정렬 · 금액은 서버 문자열 그대로.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router";
import { ApiError, apiUpload } from "../lib/api";
import { downloadFile } from "../lib/download";
import { ORDER_INTAKES_QUERY_KEY } from "../lib/order-intake";
import {
  CSV_IMPORT_PATH,
  CSV_TEMPLATE_FALLBACK_NAME,
  CSV_TEMPLATE_PATH,
  classifyUploadError,
  newUploadKey,
  precheckCsvFile,
  rowCodeLabel,
  templateErrorMessage,
  type CsvImportResult,
  type CsvReport,
  type UploadOutcome,
} from "../lib/order-intake-csv";
import { TruncationNotice } from "./truncation-notice";

interface Attempt {
  key: string;
  file: File;
}

type Outcome = { kind: "success"; result: CsvImportResult } | UploadOutcome;

const GUIDE: string[] = [
  "날짜는 2026-10-05 형식(셀 서식 yyyy-mm-dd, 시간 없이)으로 적고, PO일자·요청납기일은 비워 둘 수 없습니다.",
  "코드 열(바이어코드·바이어PO번호·바이어품번 등)은 텍스트 서식으로 두세요 — 지수 표기·앞자리 0 사라짐·#N/A 오염을 막습니다.",
  "엑셀에서 '다른 이름으로 저장 > CSV UTF-8(쉼표로 분리)'로 저장하세요(.xlsx는 올릴 수 없고, CP949로 저장하면 일부 글자가 '?'로 바뀔 수 있습니다).",
  "같은 PO번호의 줄은 인테이크 한 건으로 묶이며, 앞 5열(바이어코드~목적지시장코드)은 같은 PO의 모든 줄에서 같아야 합니다.",
  "단가는 통화의 소수 자릿수까지만 적으세요(반올림하지 않고 거부합니다).",
  "한 파일에 PO 200건·PO당 200줄·50,000행·20MB까지 올릴 수 있습니다.",
  "표 오른쪽의 빈 열은 지우고 저장하세요.",
];

export function IntakeCsvUploadPanel({ onForbidden }: { onForbidden?: () => void }) {
  const client = useQueryClient();
  const ids = { file: useId(), guide: useId(), fileError: useId(), heading: useId(), result: useId() };
  const inputRef = useRef<HTMLInputElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [retry, setRetry] = useState<Attempt | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  /** 결과가 나온 시도의 파일명 — 결과 화면에 함께 보인다(입력칸은 비워질 수 있다). */
  const [outcomeFile, setOutcomeFile] = useState<string | null>(null);
  const [reading, setReading] = useState(false);
  /** 동기 진입 가드 — 상태 갱신(isPending)은 다음 렌더에야 반영되므로 빠른 연속 클릭을 ref로 막는다. */
  const inFlight = useRef(false);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [templateBusy, setTemplateBusy] = useState(false);
  const [forbidden, setForbidden] = useState(false);

  const upload = useMutation({
    mutationFn: (attempt: Attempt) => {
      const form = new FormData();
      form.append("file", attempt.file, attempt.file.name);
      return apiUpload<CsvImportResult>(CSV_IMPORT_PATH, form, { idempotencyKey: attempt.key });
    },
    // ★ 목록 무효화는 훅 옵션에서 — mutate 콜백과 달리 관찰자(이 패널)가 사라져도 실행된다.
    onSuccess: () => client.invalidateQueries({ queryKey: ORDER_INTAKES_QUERY_KEY }),
  });
  const busy = reading || upload.isPending;
  // 읽기 실패 안내 뒤 입력칸 포커스 — 처리 중에는 입력칸이 비활성이라 렌더가 끝난 다음에 옮긴다.
  const focusInputAfterBusy = useRef(false);
  useEffect(() => {
    if (!busy && focusInputAfterBusy.current) {
      focusInputAfterBusy.current = false;
      inputRef.current?.focus();
    }
  }, [busy]);

  // 결과가 바뀌면 결과 영역으로 포커스를 옮긴다(스크린리더가 결과부터 읽는다).
  useEffect(() => {
    if (outcome !== null) resultRef.current?.focus();
  }, [outcome]);

  function latch() {
    setForbidden(true);
    onForbidden?.();
  }

  /** 입력칸을 비운다 — 같은 경로의 (고친) 파일을 다시 골라도 change 이벤트가 나게. */
  function clearPick() {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  /** 호출 전에 inFlight를 세워 둔다(onUpload·재시도). 재시도는 기존 결과를 유지한 채 버튼만 비활성화된다. */
  function send(attempt: Attempt) {
    upload.mutate(attempt, {
      onSuccess: (result) => {
        setRetry(null);
        setOutcomeFile(attempt.file.name);
        setOutcome({ kind: "success", result });
        // 같은 파일을 실수로 다시 보내지 않게 선택을 비운다(다시 보내도 서버가 FILE.DUPLICATE로 막는다).
        clearPick();
      },
      onError: (error) => {
        const next = classifyUploadError(error);
        setOutcomeFile(attempt.file.name);
        setOutcome(next);
        // 같은 키를 다시 쓰는 경우는 두 가지뿐이다 — 응답 유실·LOCK_BUSY(보낸 바이트 그대로 다시 보낸다).
        setRetry(next.kind === "response-lost" || next.kind === "lock-busy" ? attempt : null);
        // 서버가 결론을 준 거부 — 엑셀에서 고쳐 같은 경로로 저장한 파일을 다시 고를 수 있게 입력칸을 비운다.
        if (next.kind === "report" || next.kind === "file" || next.kind === "duplicate-po" || next.kind === "file-duplicate") clearPick();
        if (next.kind === "forbidden") latch();
      },
      onSettled: () => {
        inFlight.current = false;
      },
    });
  }

  function onRetry() {
    if (retry === null || inFlight.current) return;
    inFlight.current = true;
    send(retry);
  }

  function onPick(picked: File | null) {
    setFile(picked);
    setFileError(picked === null ? null : precheckCsvFile(picked));
    // 다른 파일을 골랐으면 이전 시도의 재시도는 의미가 없다(같은 키·다른 파일은 서버가 거절한다).
    if (retry !== null) {
      setRetry(null);
      setOutcome(null);
    }
  }

  async function onUpload() {
    if (inFlight.current) return;
    if (file === null) {
      setFileError("올릴 CSV 파일을 먼저 골라 주세요.");
      inputRef.current?.focus();
      return;
    }
    const problem = precheckCsvFile(file);
    if (problem !== null) {
      setFileError(problem);
      inputRef.current?.focus();
      return;
    }
    inFlight.current = true;
    // ★ 고른 순간이 아니라 보내는 순간의 바이트를 한 번 읽어 고정한다 — 엑셀에서 고쳐 저장한 파일은 File 객체가 낡아 읽기가 실패하고,
    //   '결과 다시 받기'는 처음 보낸 바이트와 같아야 한다(같은 키·다른 내용 금지).
    setReading(true);
    let frozen: File;
    try {
      const bytes = await file.arrayBuffer();
      frozen = new File([bytes], file.name, { type: file.type || "text/csv" });
    } catch {
      inFlight.current = false;
      setReading(false);
      clearPick();
      setFileError("파일을 고른 뒤 내용이 바뀌었습니다 — 파일을 다시 골라 주세요.");
      focusInputAfterBusy.current = true;
      return;
    }
    setReading(false);
    setOutcome(null);
    setRetry(null);
    // ★ 클릭마다 새 키 — 파일 내용에서 파생하지 않는다.
    send({ key: newUploadKey(), file: frozen });
  }

  async function onTemplate() {
    setTemplateError(null);
    setTemplateBusy(true);
    try {
      await downloadFile(CSV_TEMPLATE_PATH, CSV_TEMPLATE_FALLBACK_NAME);
    } catch (error) {
      setTemplateError(templateErrorMessage(error));
      if (error instanceof ApiError && error.status === 403) latch();
    } finally {
      setTemplateBusy(false);
    }
  }

  if (forbidden) {
    return (
      <p role="alert" className="mt-3 break-keep rounded border border-signal-red p-3 text-sm">
        CSV 업로드와 표준 양식은 무역·관리자만 쓸 수 있습니다. 권한이 바뀌었다면 다시 로그인해 주세요.
      </p>
    );
  }

  const describedBy = [ids.guide, fileError !== null ? ids.fileError : null].filter(Boolean).join(" ");

  return (
    <section aria-labelledby={ids.heading} className="mt-4 rounded-lg border border-gray-200 p-4 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={ids.heading} className="text-lg font-semibold">
          CSV 업로드
        </h2>
        <button
          type="button"
          onClick={() => void onTemplate()}
          disabled={templateBusy}
          className="cell-nowrap rounded border border-gray-300 px-3 py-1.5"
        >
          {templateBusy ? "내려받는 중…" : "표준 양식 내려받기"}
        </button>
      </div>
      {templateError !== null && (
        <p role="alert" className="mt-2 break-keep text-signal-red">
          {templateError}
        </p>
      )}
      <div id={ids.guide} className="mt-2 break-keep text-gray-600">
        <p>
          표준 양식(9열)에 바이어 PO를 채워 올리면 PO번호마다 오더 인테이크가 한 건씩 검토 대기로 등록됩니다. 한 행이라도 오류가 있으면 <strong>아무것도 등록하지 않고</strong> 고칠 곳을 모두 알려 드립니다.
        </p>
        <ul className="mt-1 list-disc pl-5">
          {GUIDE.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ul>
      </div>

      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label htmlFor={ids.file} className="flex flex-col gap-1">
          <span className="cell-nowrap text-gray-600">CSV 파일</span>
          <input
            ref={inputRef}
            id={ids.file}
            type="file"
            accept=".csv,text/csv"
            aria-describedby={describedBy}
            aria-invalid={fileError !== null}
            disabled={busy}
            onChange={(event) => onPick(event.target.files?.[0] ?? null)}
            className="rounded border border-gray-300 px-2 py-1.5"
          />
        </label>
        <button
          type="button"
          onClick={() => void onUpload()}
          disabled={busy}
          className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-white disabled:opacity-60"
        >
          {busy ? "업로드 중…" : "업로드"}
        </button>
      </div>
      {fileError !== null && (
        <p id={ids.fileError} role="alert" className="mt-2 break-keep text-signal-red">
          {fileError}
        </p>
      )}
      {/* 상태 알림 컨테이너는 항상 마운트한다(나중에 생긴 live region은 읽히지 않을 수 있다). */}
      <p role="status" className="mt-2 break-keep text-gray-600">
        {busy ? "파일을 검사하고 있습니다. 큰 파일은 시간이 걸릴 수 있습니다 — 창을 닫지 말아 주세요." : ""}
      </p>

      {outcome !== null && (
        <div
          ref={resultRef}
          id={ids.result}
          tabIndex={-1}
          role="region"
          aria-label="업로드 결과"
          className="mt-4 rounded border border-gray-200 p-3 outline-none focus:ring-2 focus:ring-gray-400"
        >
          {outcome.kind !== "success" && outcomeFile !== null && (
            <p className="mb-1 text-xs text-gray-600">
              파일: <span className="break-all">{outcomeFile}</span>
            </p>
          )}
          <OutcomeView outcome={outcome} retry={retry} busy={busy} onRetry={onRetry} />
        </div>
      )}
    </section>
  );
}

function OutcomeView({
  outcome,
  retry,
  busy,
  onRetry,
}: {
  outcome: Outcome;
  retry: Attempt | null;
  busy: boolean;
  onRetry: () => void;
}) {
  switch (outcome.kind) {
    case "success":
      return <SuccessView result={outcome.result} />;
    case "report":
      return <ReportView message={outcome.message} duplicateOnly={outcome.duplicateOnly} report={outcome.report} />;
    case "duplicate-po":
      return (
        <div className="break-keep">
          <h3 className="font-semibold text-signal-red">등록하지 않았습니다 — 이미 등록된 바이어 PO</h3>
          <p className="mt-1">{outcome.message}</p>
          <RepickHint />
          {outcome.intakeId !== null && (
            <Link to={`/orders/intakes/${outcome.intakeId}`} className="mt-1 inline-block underline">
              기존 인테이크 #{outcome.intakeId} 보기
            </Link>
          )}
        </div>
      );
    case "file-duplicate":
      return (
        <div className="break-keep">
          <h3 className="font-semibold text-signal-red">같은 파일이 이미 검토 대기 중입니다</h3>
          <p className="mt-1">{outcome.message}</p>
          <RepickHint />
          <ul className="mt-1 flex flex-wrap gap-3">
            {outcome.intakeIds.map((id) => (
              <li key={id}>
                <Link to={`/orders/intakes/${id}`} className="cell-nowrap underline">
                  인테이크 #{id}
                </Link>
              </li>
            ))}
            <li>
              <Link to="/orders/intakes" className="cell-nowrap underline">
                인테이크 목록 보기
              </Link>
            </li>
          </ul>
        </div>
      );
    case "lock-busy":
    case "response-lost":
      return (
        <div className="break-keep">
          <h3 className="font-semibold">{outcome.kind === "lock-busy" ? "다른 업로드가 같은 파일을 처리 중입니다" : "결과를 받지 못했습니다"}</h3>
          <p className="mt-1">{outcome.message}</p>
          {retry !== null && (
            <button
              type="button"
              onClick={onRetry}
              disabled={busy}
              className="cell-nowrap mt-2 rounded border border-gray-900 px-3 py-1.5 disabled:opacity-60"
            >
              {outcome.kind === "lock-busy" ? "같은 요청으로 다시 시도" : "결과 다시 받기"}
            </button>
          )}
        </div>
      );
    case "file":
      return (
        <div className="break-keep">
          <h3 className="font-semibold text-signal-red">등록하지 않았습니다 — 파일을 읽지 못했습니다</h3>
          <p className="mt-1">{outcome.message}</p>
          <RepickHint />
          {outcome.notes.map((note) => (
            <p key={note} className="mt-1 rounded border border-signal-amber/60 p-2">
              {note}
            </p>
          ))}
          {outcome.differences.length > 0 && (
            <div className="mt-2 overflow-x-auto rounded border border-gray-200">
              <table className="w-full text-sm">
                <caption className="sr-only">첫 행(컬럼 제목) 차이</caption>
                <thead className="bg-gray-50 text-left text-gray-600">
                  <tr>
                    <th scope="col" className="cell-nowrap px-3 py-2 text-center">열</th>
                    <th scope="col" className="cell-nowrap px-3 py-2">표준 양식</th>
                    <th scope="col" className="cell-nowrap px-3 py-2">올린 파일</th>
                  </tr>
                </thead>
                <tbody>
                  {outcome.differences.map((d) => (
                    <tr key={d.columnNo} className="border-t border-gray-100">
                      <td className="num cell-nowrap px-3 py-2">{d.columnNo}</td>
                      <td className="cell-nowrap px-3 py-2">{d.expected ?? "(없음)"}</td>
                      <td className="break-all px-3 py-2">{d.actual ?? "(없음)"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      );
    case "forbidden":
    case "other":
      return (
        <p role="alert" className="break-keep text-signal-red">
          {outcome.message}
        </p>
      );
  }
}

/** 서버가 결론을 준 거부 뒤에는 입력칸이 비워진다 — 고친 파일을 다시 고르게 안내한다. */
function RepickHint() {
  return <p className="mt-1 text-gray-600">파일을 고친 뒤 저장하고, 고친 파일을 다시 골라 주세요.</p>;
}

function SuccessView({ result }: { result: CsvImportResult }) {
  return (
    <div className="break-keep">
      <h3 className="font-semibold">
        업로드 완료 — 오더 인테이크 <span className="num">{result.group_count}</span>건을 검토 대기로 등록했습니다
      </h3>
      <p className="mt-1 text-gray-600">
        <span className="break-all">{result.original_filename}</span> · 데이터 <span className="num">{result.row_count}</span>행 · 라인{" "}
        <span className="num">{result.line_count}</span>개
      </p>
      {result.unmapped_line_count > 0 && (
        <p className="mt-2 rounded border border-signal-amber/60 p-2">
          품번 등록 필요 — SKU에 연결되지 않은 바이어 품번 라인이 <span className="num">{result.unmapped_line_count}</span>개 있습니다. 아래 각 인테이크 상세에서
          '품번 등록'으로 SKU를 연결한 뒤 확정해 주세요(오류가 아니며, 등록은 완료되었습니다).
        </p>
      )}
      <div className="mt-2 overflow-x-auto rounded border border-gray-200">
        <table className="w-full text-sm">
          <caption className="sr-only">이번 업로드로 등록된 인테이크</caption>
          <thead className="bg-gray-50 text-left text-gray-600">
            <tr>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">바이어 PO번호</th>
              <th scope="col" className="cell-nowrap px-3 py-2">바이어</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">시장</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">라인</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">품번 미등록</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">합계</th>
              <th scope="col" className="cell-nowrap px-3 py-2 text-center">첫 행</th>
            </tr>
          </thead>
          <tbody>
            {result.intakes.map((row) => (
              <tr key={row.id} className="border-t border-gray-100">
                <td className="cell-nowrap px-3 py-2 text-center">
                  <Link to={`/orders/intakes/${row.id}`} className="underline">
                    {row.buyer_po_no}
                  </Link>
                </td>
                <td className="break-keep px-3 py-2">{row.buyer_name ?? "—"}</td>
                <td className="cell-nowrap px-3 py-2 text-center">{row.dest_market_code}</td>
                <td className="num cell-nowrap px-3 py-2">{row.line_count}</td>
                <td className="num cell-nowrap px-3 py-2">{row.unmapped_line_count > 0 ? `${row.unmapped_line_count} (등록 필요)` : "0"}</td>
                <td className="num cell-nowrap px-3 py-2">
                  {row.total_text ?? "—"} {row.currency}
                </td>
                <td className="num cell-nowrap px-3 py-2">{row.first_row_no}행</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ReportView({ message, duplicateOnly, report }: { message: string; duplicateOnly: boolean; report: CsvReport }) {
  const fileLevel = report.errors.filter((e) => e.row_no === null);
  const rows = report.errors.filter((e) => e.row_no !== null);
  return (
    <div className="break-keep">
      <h3 className="font-semibold text-signal-red">
        등록하지 않았습니다 — 고칠 곳 <span className="num">{report.totalErrors}</span>건
        {duplicateOnly ? " (이미 등록된 바이어 PO)" : ""}
      </h3>
      <p className="mt-1">{message}</p>
      <p className="mt-1 text-gray-600">파일의 어떤 PO도 등록되지 않았습니다(일부만 등록되는 일은 없습니다).</p>
      <RepickHint />

      {fileLevel.length > 0 && (
        <ul aria-label="파일 전체 오류" className="mt-2 grid gap-1">
          {fileLevel.map((e, index) => (
            <li key={index} className="rounded border border-signal-amber/60 p-2">
              {e.message_ko}
            </li>
          ))}
        </ul>
      )}

      {report.countsByCode.length > 0 && (
        <ul aria-label="유형별 오류 개수" className="mt-2 flex flex-wrap gap-2">
          {report.countsByCode.map(([code, count]) => (
            <li key={code} className="cell-nowrap rounded border border-gray-300 px-2 py-0.5 text-xs">
              {rowCodeLabel(code)} <span className="num">{count}</span>건
            </li>
          ))}
        </ul>
      )}

      <TruncationNotice total={report.totalErrors} shown={report.errors.length} className="mt-2">
        외 <span className="num">{report.omittedErrors}</span>건은 위 유형별 개수에만 들어 있습니다(이미 등록된 PO·미등록 바이어·시장을 먼저 표시). 표시된 곳을 고쳐 다시 올리면 남은 오류가 이어서 나옵니다.
      </TruncationNotice>

      {rows.length > 0 && (
        <div className="mt-2 overflow-x-auto rounded border border-gray-200">
          <table className="w-full text-sm">
            <caption className="sr-only">행·열별 오류</caption>
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">행</th>
                <th scope="col" className="cell-nowrap px-3 py-2 text-center">열</th>
                <th scope="col" className="cell-nowrap px-3 py-2">유형</th>
                <th scope="col" className="cell-nowrap px-3 py-2">사유</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e, index) => (
                <tr key={index} className="border-t border-gray-100">
                  <td className="num cell-nowrap px-3 py-2">{e.row_no}</td>
                  <td className="cell-nowrap px-3 py-2 text-center">{e.column ?? "—"}</td>
                  <td className="cell-nowrap px-3 py-2">{rowCodeLabel(e.code)}</td>
                  <td className="min-w-64 break-keep px-3 py-2">{e.message_ko}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
