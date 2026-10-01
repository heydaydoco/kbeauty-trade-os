// 대기 인테이크 편집 (S3-1 PR-13b — PATCH /order-intakes/{id}: 헤더 + 라인 전체 교체).
//
// 규칙(서버가 정본):
// - 본문 `version`은 화면이 마지막으로 본/내 쓰기로 받은 기준 version(상위가 준다). 창 포커스 재조회로 입력이 지워지지 않는다 — 폼은 열 때(key) 한 번만 시드한다.
// - 바뀐 헤더 필드만 보낸다. 라인은 하나라도 바뀌면(추가·삭제·값 수정) 전체 목록을 보낸다: 기존 라인은 `id`를 달아 제자리 수정, 신규는 id 없음, 목록에 없는 기존 라인은 서버가 제외한다.
//   무변경 저장은 막는다. 거래처·통화·상태는 바꿀 수 없다(필드 없음 — 잘못 골랐으면 거부 후 재등록).
// - 형식 외 검증(단가 자릿수·0 초과·요청납기·같은 SKU 중복·중복 PO)은 서버가 판정하고 한국어 안내를 그대로 보인다.
// - 더블클릭은 ref 잠금(mutationFn finally에서 해제). networkMode:"always".

import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router";
import { apiFetch } from "../lib/api";
import {
  intakeErrorMessage,
  isIntakeRecoverable,
  isWriteForbidden,
  lineBodies,
  newLineForm,
  parsePoOccupant,
  validateLineForms,
  type IntakeDetail,
  type IntakeEditLineBody,
  type LineForm,
} from "../lib/order-intake";
import { usePagedQuery } from "../lib/paging";
import type { Market } from "../routes/markets";
import { LineEditor } from "./intake-line-editor";

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

interface UserLookup {
  id: number;
  display_name: string;
}

const seedLines = (intake: IntakeDetail): LineForm[] =>
  intake.lines.map((line) =>
    newLineForm({
      id: line.id,
      code: line.buyer_item_code,
      qty: String(line.quantity),
      price: line.unit_price_text,
      delivery: line.requested_delivery_date ?? "",
    }),
  );

/** 시드와 현재 라인 목록이 같은가 — 순서·id·값 전부 비교(문자열 그대로, 산술 없음). */
function sameLines(a: LineForm[], b: LineForm[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((x, i) => {
    const y = b[i] as LineForm;
    return x.id === y.id && x.code.trim() === y.code.trim() && x.qty.trim() === y.qty.trim() && x.price.trim() === y.price.trim() && x.delivery === y.delivery;
  });
}

export function IntakeEditForm({
  intake,
  version,
  onSaved,
  onForbidden,
  onReload,
}: {
  intake: IntakeDetail;
  /** 쓰기에 싣는 기준 version(화면이 본 값). */
  version: number;
  /** 저장 성공 — 상위가 응답을 화면 상태·기준 version으로 반영한다. */
  onSaved: (next: IntakeDetail) => void;
  onForbidden: () => void;
  /** 충돌(409) 안내의 '최신 내용 불러오기'. */
  onReload: () => void;
}) {
  const markets = usePagedQuery<Market>(["markets", "select"], "/v1/markets?size=200");
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200");

  // 시드는 마운트 때 한 번 — 상위가 key로 다시 시드한다(저장 성공·최신 내용 불러오기).
  const seed = useRef({ lines: seedLines(intake), poNo: intake.buyer_po_no, poDate: intake.buyer_po_date ?? "", market: intake.dest_market_code, assignee: String(intake.assignee_id) });
  const [poNo, setPoNo] = useState(seed.current.poNo);
  const [poDate, setPoDate] = useState(seed.current.poDate);
  const [market, setMarket] = useState(seed.current.market);
  const [assignee, setAssignee] = useState(seed.current.assignee);
  const [lines, setLines] = useState<LineForm[]>(() => seed.current.lines.map((l) => ({ ...l })));
  const [formError, setFormError] = useState<string | null>(null);
  const lock = useRef(false);

  const save = useMutation({
    networkMode: "always",
    mutationFn: async (body: Record<string, unknown>) => {
      try {
        return await apiFetch<IntakeDetail>(`/v1/order-intakes/${intake.id}`, { method: "PATCH", body });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: (next) => onSaved(next),
    onError: (error) => {
      if (isWriteForbidden(error)) onForbidden();
    },
  });

  const s = seed.current;
  const headerChanged = {
    buyer_po_no: poNo.trim() !== s.poNo,
    buyer_po_date: poDate !== s.poDate,
    dest_market_code: market !== s.market,
    assignee_id: assignee !== s.assignee,
  };
  const linesChanged = !sameLines(s.lines, lines);
  const changed = Object.values(headerChanged).some(Boolean) || linesChanged;

  function submit() {
    if (lock.current) return;
    if (!changed) {
      setFormError("바뀐 내용이 없습니다.");
      return;
    }
    if (poNo.trim() === "") {
      setFormError("바이어 PO번호를 입력해 주세요.");
      return;
    }
    if (linesChanged) {
      const problem = validateLineForms(lines);
      if (problem !== null) {
        setFormError(problem);
        return;
      }
    }
    const body: Record<string, unknown> = { version };
    if (headerChanged.buyer_po_no) body.buyer_po_no = poNo.trim();
    if (headerChanged.buyer_po_date) body.buyer_po_date = poDate === "" ? null : poDate;
    if (headerChanged.dest_market_code) body.dest_market_code = market;
    if (headerChanged.assignee_id) body.assignee_id = Number(assignee);
    if (linesChanged) {
      const bodies = lineBodies(lines);
      body.lines = bodies.map<IntakeEditLineBody>((line, i) => {
        const id = (lines[i] as LineForm).id;
        return id === null ? line : { id, ...line };
      });
    }
    setFormError(null);
    lock.current = true;
    save.mutate(body);
  }

  function touched() {
    setFormError(null);
    save.reset();
  }

  const marketItems = markets.data?.items ?? [];
  const userItems = users.data?.items ?? [];
  const occupant = parsePoOccupant(save.error);
  const serverError = save.error ? intakeErrorMessage(save.error, "edit") : null;

  return (
    <form
      aria-label="인테이크 편집"
      className="grid gap-4 rounded-lg border border-gray-200 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <h2 className="text-lg font-semibold">내용 수정</h2>
      <p className="break-keep text-xs text-gray-500">
        바이어·통화는 등록 뒤 바꿀 수 없습니다(잘못 골랐으면 거부 후 다시 등록). 바뀐 라인만 품번을 다시 해석하고, 바뀌지 않은 라인의 해석 상태는 &apos;품번 다시 확인&apos;을 누를 때까지 그대로입니다.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">바이어 PO번호</span>
          <input
            value={poNo}
            maxLength={200}
            onChange={(e) => {
              touched();
              setPoNo(e.target.value);
            }}
            disabled={save.isPending}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">바이어 PO 일자</span>
          <input
            type="date"
            value={poDate}
            onChange={(e) => {
              touched();
              setPoDate(e.target.value);
            }}
            disabled={save.isPending}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">도착 시장</span>
          <select
            value={market}
            onChange={(e) => {
              touched();
              setMarket(e.target.value);
            }}
            disabled={save.isPending}
            className={inputClass}
          >
            {!marketItems.some((m) => m.code === market) && <option value={market}>{market}</option>}
            {marketItems.map((m) => (
              <option key={m.code} value={m.code}>
                {m.name_ko} ({m.code})
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-gray-600">담당자</span>
          <select
            value={assignee}
            onChange={(e) => {
              touched();
              setAssignee(e.target.value);
            }}
            disabled={save.isPending}
            className={inputClass}
          >
            {!userItems.some((u) => String(u.id) === assignee) && <option value={assignee}>담당자 #{assignee}</option>}
            {userItems.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <LineEditor
        lines={lines}
        onChange={(next) => {
          touched();
          setLines(next);
        }}
        disabled={save.isPending}
        currency={intake.currency}
      />

      {(formError || serverError) && (
        <div role="alert" className="break-keep text-sm text-signal-red">
          <p>{formError ?? serverError}</p>
          {formError === null && occupant?.intakeId != null && (
            <p className="mt-1">
              <Link to={`/orders/intakes/${occupant.intakeId}`} className="underline">
                점유 중인 인테이크 #{occupant.intakeId} 보기
              </Link>
            </p>
          )}
          {formError === null && isIntakeRecoverable(save.error) && (
            <button type="button" onClick={onReload} className="cell-nowrap mt-2 rounded border border-gray-300 px-3 py-1 text-gray-900">
              최신 내용 불러오기
            </button>
          )}
        </div>
      )}

      <div>
        <button type="submit" disabled={save.isPending || !changed} className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50">
          {save.isPending ? "저장 중…" : "수정 저장"}
        </button>
      </div>
    </form>
  );
}
