// 오더 인테이크 수동 등록 (S3-1 PR-13b — design-D D2·D7 / ADR-0071).
//
// 규칙(서버가 정본, 화면은 편의 — §18.1):
// - 등록은 항상 '대기' 상태로만 착지한다 — 상태·SKU·PO 비교 키 입력란이 없다(품번→SKU 해석은 서버).
// - 입력 검사는 형식만(필수·정수 수량·금액 문자열). 단가 자릿수·0 초과·요청납기·같은 SKU 라인 중복·바이어 유형·시장 활성·복제 원본 자격은 서버 응답의 한국어 안내를 그대로 보인다.
// - 멱등 키: 본문 → 키 Map(같은 본문 재시도·더블클릭 = 같은 키, 본문이 달라질 때만 새 키), 성공하면 비운다. 더블클릭은 ref 잠금을 mutationFn finally에서 푼다. networkMode:"always".
// - 중복 바이어 PO(409)의 점유 문서 번호·상태는 서버가 줄 때만 표시한다.
// - 복제 재접수: 주소에 `?copied_from_so_id=`가 있을 때만 필드를 보인다(취소된 같은 바이어의 수주여야 하며 서버가 확인한다).

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { LineEditor } from "../components/intake-line-editor";
import { SearchSelect } from "../components/search-select";
import { apiFetch } from "../lib/api";
import { keyFor } from "../lib/confirm";
import { newGateKey } from "../lib/gate";
import { useCurrencies } from "../lib/money";
import {
  ORDER_INTAKES_QUERY_KEY,
  intakeErrorMessage,
  lineBodies,
  newLineForm,
  orderIntakeDetailKey,
  parsePoOccupant,
  validateLineForms,
  type IntakeCreateBody,
  type IntakeDetail,
  type LineForm,
} from "../lib/order-intake";
import { usePagedQuery } from "../lib/paging";
import { hasRole, useSession } from "../lib/session";
import { MARKETS_SELECT_PATH, type Market } from "./markets";
import type { Partner } from "./partners";

const inputClass = "rounded border border-gray-300 px-3 py-2 text-sm";

interface UserLookup {
  id: number;
  display_name: string;
}

export function OrderIntakeCreatePage() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { me } = useSession();
  const canWrite = hasRole(me, "TRADE");
  const [search] = useSearchParams();
  const copiedRaw = search.get("copied_from_so_id");
  const copiedFrom = copiedRaw !== null && /^[1-9][0-9]*$/.test(copiedRaw) ? Number(copiedRaw) : null;

  const currencies = useCurrencies();
  const markets = usePagedQuery<Market>(["markets", "select"], MARKETS_SELECT_PATH, canWrite);
  const users = usePagedQuery<UserLookup>(["users", "lookup"], "/v1/users/lookup?size=200", canWrite);

  const [buyer, setBuyer] = useState<Partner | null>(null);
  const [currency, setCurrency] = useState("");
  const [market, setMarket] = useState("");
  const [poNo, setPoNo] = useState("");
  const [poDate, setPoDate] = useState("");
  const [assignee, setAssignee] = useState("");
  const [lines, setLines] = useState<LineForm[]>(() => [newLineForm()]);
  const [formError, setFormError] = useState<string | null>(null);

  const lock = useRef(false);
  const keys = useRef(new Map<string, string>());

  const create = useMutation({
    // 오프라인이어도 요청을 보내 본다 — 기본(online)은 paused로 멈춰 화면이 갇힌다.
    networkMode: "always",
    // ★ 잠금 해제는 finally — 요청 중 reset()·언마운트에도 풀린다.
    mutationFn: async (input: { body: IntakeCreateBody; key: string }) => {
      try {
        return await apiFetch<IntakeDetail>("/v1/order-intakes", { method: "POST", idempotencyKey: input.key, body: input.body });
      } finally {
        lock.current = false;
      }
    },
    onSuccess: (created) => {
      keys.current.clear();
      client.setQueryData(orderIntakeDetailKey(created.id), created);
      void client.invalidateQueries({ queryKey: ORDER_INTAKES_QUERY_KEY });
      void navigate(`/orders/intakes/${created.id}`);
    },
  });

  function touched() {
    setFormError(null);
    create.reset();
  }

  function buildBody(): IntakeCreateBody | null {
    if (buyer === null) return fail("바이어를 선택해 주세요.");
    if (currency === "") return fail("통화를 선택해 주세요.");
    if (market === "") return fail("도착 시장을 선택해 주세요.");
    if (poNo.trim() === "") return fail("바이어 PO번호를 입력해 주세요.");
    const problem = validateLineForms(lines);
    if (problem !== null) return fail(problem);
    const body: IntakeCreateBody = {
      buyer_partner_id: buyer.id,
      buyer_po_no: poNo.trim(),
      buyer_po_date: poDate === "" ? null : poDate,
      currency,
      dest_market_code: market,
      lines: lineBodies(lines),
    };
    if (assignee !== "") body.assignee_id = Number(assignee);
    if (copiedFrom !== null) body.copied_from_so_id = copiedFrom;
    return body;
  }
  function fail(message: string): null {
    setFormError(message);
    return null;
  }

  function submit() {
    if (lock.current) return;
    const body = buildBody();
    if (body === null) return;
    // 키를 먼저 만든다 — 키 생성이 던져도 잠금이 서지 않아 고착되지 않는다.
    const key = keyFor(keys.current, JSON.stringify(body), newGateKey);
    lock.current = true;
    setFormError(null);
    create.mutate({ body, key });
  }

  if (!canWrite) {
    return (
      <section>
        <Link to="/orders/intakes" className="cell-nowrap text-sm text-gray-500 underline">← 인테이크 목록</Link>
        <p role="note" className="mt-3 break-keep text-sm text-gray-700">인테이크 등록은 무역·관리자만 할 수 있습니다. 목록에서 조회만 할 수 있습니다.</p>
      </section>
    );
  }

  const userItems = users.data?.items ?? [];
  const occupant = parsePoOccupant(create.error);
  const serverError = create.error ? intakeErrorMessage(create.error, "create") : null;

  return (
    <section>
      <Link to="/orders/intakes" className="cell-nowrap text-sm text-gray-500 underline">← 인테이크 목록</Link>
      <h1 className="mt-2 text-2xl font-bold">오더 인테이크 수동 등록</h1>
      <p className="mt-1 break-keep text-sm text-gray-500">
        받은 바이어 PO를 그대로 옮겨 적습니다. 등록하면 &apos;대기&apos; 상태가 되고, 바이어 품번은 서버가 SKU로 해석합니다(미매핑은 상세에서 품번을 등록해 해소). 같은 바이어의 같은 PO번호는 한 번만 받을 수 있습니다.
      </p>

      <form
        className="mt-5 grid gap-4"
        aria-label="오더 인테이크 등록"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        {copiedFrom !== null && (
          <p role="note" className="break-keep rounded border border-gray-300 p-3 text-sm">
            복제 재접수: 취소된 원본 수주{" "}
            <Link to={`/sales-orders/${copiedFrom}`} className="underline">
              #{copiedFrom}
            </Link>
            를 근거로 등록합니다. 원본이 같은 바이어의 취소된 수주인지는 서버가 확인합니다.
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">바이어 *</span>
            <SearchSelect<Partner>
              label="바이어"
              path="/v1/partners"
              params={{ type: "BUYER" }}
              queryKey={["partners"]}
              value={buyer}
              onChange={(next) => {
                touched();
                setBuyer(next);
              }}
              getKey={(item) => item.id}
              getLabel={(item) => `${item.name_ko} (${item.partner_code})`}
              disabled={create.isPending}
            />
            <span className="break-keep text-xs text-gray-500">바이어 유형 거래처만 선택할 수 있습니다. 등록 뒤에는 바꿀 수 없습니다(잘못 골랐으면 거부 후 다시 등록).</span>
          </div>
          <div className="flex flex-col gap-1 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">통화 *</span>
              <select
                value={currency}
                aria-describedby="intake-currency-hint"
                onChange={(e) => {
                  touched();
                  setCurrency(e.target.value);
                }}
                disabled={create.isPending}
                className={inputClass}
              >
                <option value="">선택</option>
                {(currencies.data?.items ?? []).map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.code}
                  </option>
                ))}
              </select>
            </label>
            <span id="intake-currency-hint" className="break-keep text-xs text-gray-500">
              통화 규칙(자릿수·단가 형식)은 서버가 확인합니다. 등록 뒤에는 바꿀 수 없습니다.
            </span>
          </div>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">도착 시장 *</span>
            <select
              value={market}
              onChange={(e) => {
                touched();
                setMarket(e.target.value);
              }}
              disabled={create.isPending}
              className={inputClass}
            >
              <option value="">선택</option>
              {(markets.data?.items ?? []).map((m) => (
                <option key={m.code} value={m.code}>
                  {m.name_ko} ({m.code})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-gray-600">바이어 PO번호 *</span>
            <input
              value={poNo}
              maxLength={200}
              onChange={(e) => {
                touched();
                setPoNo(e.target.value);
              }}
              disabled={create.isPending}
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
              disabled={create.isPending}
              className={inputClass}
            />
          </label>
          <div className="flex flex-col gap-1 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-gray-600">담당자 (비우면 나)</span>
              <select
                value={assignee}
                onChange={(e) => {
                  touched();
                  setAssignee(e.target.value);
                }}
                disabled={create.isPending}
                className={inputClass}
              >
                <option value="">나</option>
                {userItems.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.display_name}
                  </option>
                ))}
              </select>
            </label>
            {users.data && users.data.total > userItems.length && (
              <span role="status" className="text-xs text-gray-500">
                {users.data.total}명 중 {userItems.length}명만 표시합니다.
              </span>
            )}
          </div>
        </div>

        <LineEditor
          lines={lines}
          onChange={(next) => {
            touched();
            setLines(next);
          }}
          disabled={create.isPending}
          currency={currency}
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
          </div>
        )}

        <div>
          <button type="submit" disabled={create.isPending} className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-sm text-white disabled:opacity-50">
            {create.isPending ? "등록 중…" : "인테이크 등록"}
          </button>
        </div>
      </form>
    </section>
  );
}
