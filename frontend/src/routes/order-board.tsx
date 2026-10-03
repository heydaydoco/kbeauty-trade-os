// 오더 보드 `/orders/board` (S3-1 PR-15b — design-D D6 / DESIGN §7.4 [M4] / ADR-0066·0067).
//
// ★ 열·카드·건수(total)·has_more는 서버 `GET /order-board` 응답 그대로다 — 화면이 열을 다시 나누거나 카드를 거르지 않는다.
//   열당 50건을 넘으면 잘림을 알리고(TruncationNotice) '더 보기'(`GET /order-board/items` Page 드릴다운)로 나머지를 본다.
// ★ 카드에는 원가·여신·게이트가 없다(서버 응답 모델에 필드 부재) — 게이트는 수주 상세의 확정·게이트 패널에서 본다.
// ★ 벌크(인테이크 확정·수주 확정·담당자 지정)는 무역·관리자만 보인다(표시 편의 — 서버가 라우트·서비스·건마다 다시 판정한다).
//   승인·예외 승인은 벌크로 부여·우회할 수 없다 — 막힌 건은 결과 모달의 상세 링크에서 개별 처리한다.
// ★ 멱등 키: 클릭마다 새 키. 응답 유실(504·네트워크)일 때만 '결과 다시 받기'가 **같은 키·같은 본문**을 재전송한다.
// ★ 담당자 필터는 무역·관리자에게만 보인다 — 담당자 검색(`users/lookup`)이 그 역할 전용이다(조회 역할은 403).
// 규약: 한국어 break-keep · 좁은 셀·헤더 nowrap · 숫자 가운데 정렬 · 금액은 서버 문자열 그대로 · 시각은 KST.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { BulkResultDialog } from "../components/board-bulk-result";
import { BoardSavedFilters } from "../components/board-saved-filters";
import { ConfirmDialog } from "../components/confirm-dialog";
import { ListPager } from "../components/list-pager";
import { ListState } from "../components/list-state";
import { SearchSelect } from "../components/search-select";
import { TruncationNotice } from "../components/truncation-notice";
import { apiFetch } from "../lib/api";
import { errorMessage } from "../lib/api-errors";
import { toKstDisplay } from "../lib/datetime";
import { downloadFile } from "../lib/download";
import { useCurrencies } from "../lib/money";
import {
  ACTION_LABEL,
  BULK_MAX_TARGETS,
  EMPTY_FILTER,
  FILTER_DATE_MAX,
  FILTER_DATE_MIN,
  ORDER_BOARD_QUERY_KEY,
  QUERY_MAX,
  boardExportPath,
  boardItemsPath,
  boardPath,
  buildBulkRequest,
  bulkErrorMessage,
  cardKey,
  checkFilter,
  detailPath,
  filterFieldErrors,
  filterToQuery,
  isResponseLost,
  normalizeFilter,
  type BoardCard,
  type BoardColumn,
  type BoardFilter,
  type BoardStage,
  type BulkAction,
  type BulkReport,
  type BulkRequest,
  type FieldErrors,
  type OrderBoard,
} from "../lib/order-board";
import { ORDER_INTAKES_QUERY_KEY } from "../lib/order-intake";
import { FRESH_EVERY_TIME, usePagedList, usePagedQuery } from "../lib/paging";
import { SALES_ORDERS_QUERY_KEY } from "../lib/sales-order";
import { hasRole, useSession } from "../lib/session";
import { MARKETS_SELECT_PATH, type Market } from "./markets";

/** 보드·드릴다운 데이터 키 접두 — 벌크 뒤 이 접두로 무효화한다(저장 필터 키와 분리). */
export const BOARD_DATA_KEY = [...ORDER_BOARD_QUERY_KEY, "data"] as const;

interface UserLookup {
  id: number;
  display_name: string;
}

/** 검색형 선택의 값 — 저장 필터를 적용하면 이름 없이 id만 오므로 이름은 아는 만큼(카드의 이름) 채운다. */
interface BuyerOption {
  id: number;
  name_ko: string;
  partner_code?: string;
}

interface Draft {
  q: string;
  buyer: BuyerOption | null;
  assignee: UserLookup | null;
  currency: string;
  dest_market_code: string;
  created_from: string;
  created_to: string;
}

const EMPTY_DRAFT: Draft = {
  q: "",
  buyer: null,
  assignee: null,
  currency: "",
  dest_market_code: "",
  created_from: "",
  created_to: "",
};

function draftToFilter(draft: Draft, canFilterAssignee: boolean): BoardFilter {
  return normalizeFilter({
    q: draft.q,
    buyer_partner_id: draft.buyer?.id ?? null,
    assignee_id: canFilterAssignee ? (draft.assignee?.id ?? null) : null,
    currency: draft.currency,
    dest_market_code: draft.dest_market_code,
    created_from: draft.created_from,
    created_to: draft.created_to,
  });
}

/** 벌크 시도 1건 — 같은 키 재전송은 이 본문 그대로만 보낸다. */
interface BulkAttempt {
  request: BulkRequest;
  key: string;
  labels: Map<string, string>;
}

const newBulkKey = (): string => crypto.randomUUID();

/** 필드 옆 오류 — 라벨 밖에 둔다(라벨 이름이 오류 문구로 오염되지 않게), 입력칸은 aria-describedby로 가리킨다. */
function FieldError({ id, message }: { id?: string; message: string | undefined }) {
  if (!message) return null;
  return (
    <span id={id} role="alert" className="break-keep text-xs text-signal-red">
      {message}
    </span>
  );
}

// ── 카드 ─────────────────────────────────────────────────────────────────────

function BoardCardView({
  card,
  selectable,
  selected,
  onToggle,
}: {
  card: BoardCard;
  selectable: boolean;
  selected: boolean;
  onToggle: (card: BoardCard) => void;
}) {
  return (
    <li className={`rounded border bg-white p-2 text-sm dark:bg-black ${selected ? "border-gray-900" : "border-gray-200"}`}>
      <div className="flex items-start gap-2">
        {selectable && (
          <input
            type="checkbox"
            aria-label={`${card.ref_label} 선택`}
            checked={selected}
            onChange={() => onToggle(card)}
            className="mt-1"
          />
        )}
        <div className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2">
            <Link to={detailPath(card.kind, card.id)} className="cell-nowrap font-medium underline">
              {card.ref_label}
            </Link>
            <span className="cell-nowrap rounded border border-gray-300 px-1 text-xs text-gray-600">
              {card.kind === "INTAKE" ? "인테이크" : "수주"}
            </span>
          </span>
          <span className="block break-keep">{card.buyer_name ?? "바이어명 없음"}</span>
          {card.buyer_po_no && <span className="block break-keep text-xs text-gray-600">PO {card.buyer_po_no}</span>}
          <span className="mt-1 flex flex-wrap gap-x-2 text-xs text-gray-600">
            <span className="cell-nowrap">
              라인 <span className="num">{card.line_count}</span>
            </span>
            <span className="num cell-nowrap">
              {card.total_text} {card.currency}
            </span>
          </span>
          <span className="flex flex-wrap gap-x-2 text-xs text-gray-500">
            <span className="cell-nowrap">
              접수 후 <span className="num">{card.age_days}</span>일
            </span>
            <span className="cell-nowrap">담당 {card.assignee_name ?? `#${card.assignee_id}`}</span>
          </span>
          <span className="block text-xs text-gray-400">수정 {toKstDisplay(card.updated_at)}</span>
        </div>
      </div>
    </li>
  );
}

// ── 드릴다운(더 보기) ─────────────────────────────────────────────────────────

function BoardDrilldown({
  stage,
  label,
  filter,
  selectable,
  isSelected,
  onToggle,
  onClose,
}: {
  stage: BoardStage;
  label: string;
  filter: BoardFilter;
  selectable: boolean;
  isSelected: (card: BoardCard) => boolean;
  onToggle: (card: BoardCard) => void;
  onClose: () => void;
}) {
  const query = filterToQuery(filter);
  const list = usePagedList<BoardCard>([...BOARD_DATA_KEY, "items", stage, query], boardItemsPath(filter, stage), true, FRESH_EVERY_TIME);
  const [csvError, setCsvError] = useState<string | null>(null);

  async function exportStage() {
    setCsvError(null);
    try {
      await downloadFile(boardExportPath(filter, stage), "오더보드.csv");
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다."));
    }
  }

  return (
    <section aria-label={`${label} 전체 보기`} className="mt-6 rounded-lg border border-gray-200 p-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="cell-nowrap text-lg font-semibold">{label} 전체 보기</h2>
        <span className="flex gap-2">
          <button type="button" onClick={() => void exportStage()} className="cell-nowrap rounded border border-gray-300 px-3 py-1 text-sm">
            이 열 CSV
          </button>
          <button type="button" onClick={onClose} className="cell-nowrap rounded border border-gray-300 px-3 py-1 text-sm">
            닫기
          </button>
        </span>
      </header>
      {csvError && (
        <p role="alert" className="mt-2 text-sm text-signal-red">
          {csvError}
        </p>
      )}
      <ListPager data={list.data} page={list.page} onPageChange={list.setPage} className="mt-2" />
      <ListState isPending={list.isPending} error={list.error} isEmpty={list.data?.items.length === 0} emptyHint="이 열에 해당하는 건이 없습니다.">
        <ul className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {list.data?.items.map((card) => (
            <BoardCardView key={cardKey(card)} card={card} selectable={selectable} selected={isSelected(card)} onToggle={onToggle} />
          ))}
        </ul>
      </ListState>
    </section>
  );
}

// ── 열 ───────────────────────────────────────────────────────────────────────

function BoardColumnView({
  column,
  selectable,
  isSelected,
  onToggle,
  onSelectAll,
  onMore,
}: {
  column: BoardColumn;
  selectable: boolean;
  isSelected: (card: BoardCard) => boolean;
  onToggle: (card: BoardCard) => void;
  onSelectAll: (cards: BoardCard[]) => void;
  onMore: (stage: BoardStage, label: string) => void;
}) {
  return (
    <section aria-label={`${column.label_ko} 열`} className="w-64 shrink-0 rounded-lg bg-gray-50 p-2 dark:bg-gray-900">
      <h2 className="cell-nowrap mb-1 text-sm font-semibold">
        {column.label_ko} <span className="num font-normal text-gray-500">{column.total}</span>
      </h2>
      {column.has_more && (
        <TruncationNotice total={column.total} shown={column.items.length} className="mb-2">
          &lsquo;더 보기&rsquo;로 나머지를 봅니다.
        </TruncationNotice>
      )}
      <div className="mb-2 flex flex-wrap gap-2 text-xs">
        {selectable && column.items.length > 0 && (
          <button type="button" onClick={() => onSelectAll(column.items)} className="cell-nowrap underline">
            보이는 카드 모두 선택
          </button>
        )}
        {column.has_more && (
          <button
            type="button"
            aria-label={`${column.label_ko} 더 보기`}
            onClick={() => onMore(column.stage, column.label_ko)}
            className="cell-nowrap underline"
          >
            더 보기
          </button>
        )}
      </div>
      {column.items.length === 0 ? (
        <p className="text-xs text-gray-500">해당 건 없음</p>
      ) : (
        <ul className="space-y-2">
          {column.items.map((card) => (
            <BoardCardView key={cardKey(card)} card={card} selectable={selectable} selected={isSelected(card)} onToggle={onToggle} />
          ))}
        </ul>
      )}
    </section>
  );
}

// ── 화면 ─────────────────────────────────────────────────────────────────────

export function OrderBoardPage() {
  const { me } = useSession();
  const client = useQueryClient();
  // 벌크·담당자 검색은 무역·관리자 전용(서버가 정본) — 그 밖의 역할에는 숨긴다(403 소음·막다른 버튼 방지).
  const canBulk = hasRole(me, "TRADE");

  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [applied, setApplied] = useState<BoardFilter>(EMPTY_FILTER);
  const [clientErrors, setClientErrors] = useState<FieldErrors>({});
  const [drill, setDrill] = useState<{ stage: BoardStage; label: string } | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);

  const query = filterToQuery(applied);
  const board = useQuery({
    queryKey: [...BOARD_DATA_KEY, "board", query],
    queryFn: () => apiFetch<OrderBoard>(boardPath(applied)),
    ...FRESH_EVERY_TIME,
  });
  const currencies = useCurrencies();
  const markets = usePagedQuery<Market>(["markets", "options"], MARKETS_SELECT_PATH);

  const serverErrors = filterFieldErrors(board.error) ?? {};
  const fieldErrors: FieldErrors = { ...serverErrors, ...clientErrors };

  // ── 선택 ──
  const [selected, setSelected] = useState<Map<string, BoardCard>>(() => new Map());
  const isSelected = (card: BoardCard) => selected.has(cardKey(card));
  const toggle = (card: BoardCard) =>
    setSelected((previous) => {
      const next = new Map(previous);
      if (next.has(cardKey(card))) next.delete(cardKey(card));
      else next.set(cardKey(card), card);
      return next;
    });
  const selectAll = (cards: BoardCard[]) =>
    setSelected((previous) => {
      const next = new Map(previous);
      for (const card of cards) next.set(cardKey(card), card);
      return next;
    });
  const selectedCards = [...selected.values()];
  const intakeCount = selectedCards.filter((card) => card.kind === "INTAKE").length;
  const soCount = selectedCards.filter((card) => card.kind === "SO").length;
  const [assignee, setAssignee] = useState<UserLookup | null>(null);

  // ── 벌크 ──
  const [pendingAction, setPendingAction] = useState<BulkAction | null>(null);
  const [attempt, setAttempt] = useState<BulkAttempt | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [report, setReport] = useState<BulkReport | null>(null);
  const [bulkError, setBulkError] = useState<string | null>(null);
  const [lost, setLost] = useState(false);

  const refreshBoard = () => {
    setSelected(new Map());
    void client.invalidateQueries({ queryKey: BOARD_DATA_KEY });
  };

  const bulk = useMutation({
    mutationFn: ({ request, key }: { request: BulkRequest; key: string }) =>
      apiFetch<BulkReport>("/v1/order-board/bulk", { method: "POST", body: request, idempotencyKey: key }),
    onSuccess: (result) => {
      setReport(result);
      setBulkError(null);
      setLost(false);
      setSelected(new Map());
      void client.invalidateQueries({ queryKey: BOARD_DATA_KEY });
      void client.invalidateQueries({ queryKey: SALES_ORDERS_QUERY_KEY });
      void client.invalidateQueries({ queryKey: ORDER_INTAKES_QUERY_KEY });
    },
    onError: (caught) => {
      setReport(null);
      setBulkError(bulkErrorMessage(caught));
      // 응답을 받은 거절(4xx·키 충돌 등)은 재전송 대상이 아니다 — 다음 시도는 새 클릭·새 키.
      setLost(isResponseLost(caught));
    },
  });

  function run(action: BulkAction) {
    const request = buildBulkRequest(action, selectedCards, action === "ASSIGN" ? assignee?.id : undefined);
    const labels = new Map(selectedCards.map((card) => [cardKey(card), card.ref_label]));
    // 클릭마다 새 키 — 같은 키는 '결과 다시 받기'(같은 본문 재전송)에만 쓴다.
    const next: BulkAttempt = { request, key: newBulkKey(), labels };
    setAttempt(next);
    setReport(null);
    setBulkError(null);
    setLost(false);
    setDialogOpen(true);
    setPendingAction(null);
    bulk.mutate({ request: next.request, key: next.key });
  }

  function resend() {
    if (attempt === null || !lost) return;
    setDialogOpen(true);
    setBulkError(null);
    bulk.mutate({ request: attempt.request, key: attempt.key });
  }

  function closeResult() {
    setDialogOpen(false);
    if (!lost) {
      setAttempt(null);
      setReport(null);
      setBulkError(null);
    }
  }

  // ── 필터 ──
  function submitFilter(event: FormEvent) {
    event.preventDefault();
    const next = draftToFilter(draft, canBulk);
    const problems = checkFilter(next);
    setClientErrors(problems);
    if (Object.keys(problems).length > 0) return;
    setApplied(next);
    setDrill(null);
  }

  function resetFilter() {
    setDraft(EMPTY_DRAFT);
    setClientErrors({});
    setApplied(EMPTY_FILTER);
    setDrill(null);
  }

  function applySaved(filter: BoardFilter) {
    const cards = board.data?.columns.flatMap((column) => column.items) ?? [];
    const buyerName = cards.find((card) => card.buyer_partner_id === filter.buyer_partner_id)?.buyer_name;
    const assigneeName = cards.find((card) => card.assignee_id === filter.assignee_id)?.assignee_name;
    const next = normalizeFilter({ ...EMPTY_FILTER, ...filter, assignee_id: canBulk ? filter.assignee_id : null });
    setDraft({
      q: next.q ?? "",
      buyer: next.buyer_partner_id === null ? null : { id: next.buyer_partner_id, name_ko: buyerName ?? `거래처 #${next.buyer_partner_id}` },
      assignee: next.assignee_id === null ? null : { id: next.assignee_id, display_name: assigneeName ?? `담당자 #${next.assignee_id}` },
      currency: next.currency ?? "",
      dest_market_code: next.dest_market_code ?? "",
      created_from: next.created_from ?? "",
      created_to: next.created_to ?? "",
    });
    setClientErrors({});
    setApplied(next);
    setDrill(null);
  }

  async function exportCsv() {
    setCsvError(null);
    try {
      await downloadFile(boardExportPath(applied), "오더보드.csv");
    } catch (caught) {
      setCsvError(errorMessage(caught, "내려받지 못했습니다."));
    }
  }

  const tooMany = (count: number) => count > BULK_MAX_TARGETS;
  const confirmCount =
    pendingAction === null ? 0 : buildBulkRequest(pendingAction, selectedCards, assignee?.id).targets.length;
  const skippedKinds = pendingAction === null ? 0 : selectedCards.length - confirmCount;

  return (
    <section>
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">오더 보드</h1>
          <p className="mt-1 break-keep text-sm text-gray-500">
            접수 대기 인테이크부터 확정된 수주까지 한눈에 봅니다. 카드를 누르면 상세 화면으로 갑니다. 원가·여신·게이트 판정은 상세 화면에서 확인합니다.
          </p>
        </div>
        <button type="button" onClick={() => void exportCsv()} className="cell-nowrap rounded border border-gray-300 px-3 py-2 text-sm">
          CSV 내보내기
        </button>
      </header>
      {csvError && (
        <p role="alert" className="mt-2 break-keep text-sm text-signal-red">
          {csvError}
        </p>
      )}

      <form role="search" aria-label="오더 보드 필터" onSubmit={submitFilter} className="mt-4 flex flex-wrap items-start gap-3 text-sm">
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">검색어</span>
            <input
              name="q"
              value={draft.q}
              maxLength={QUERY_MAX * 2}
              aria-invalid={fieldErrors.q !== undefined}
              aria-describedby={fieldErrors.q ? "board-filter-q-error" : undefined}
              placeholder="거래처명·PO번호·수주번호·IN-번호"
              onChange={(event) => setDraft({ ...draft, q: event.target.value })}
              className="w-64 rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <FieldError id="board-filter-q-error" message={fieldErrors.q} />
        </div>
        <div className="flex w-56 flex-col gap-1">
          <span className="cell-nowrap text-gray-600">바이어</span>
          <SearchSelect<BuyerOption>
            label="바이어"
            path="/v1/partners"
            params={{ type: "BUYER" }}
            queryKey={["partners"]}
            value={draft.buyer}
            onChange={(next) => setDraft({ ...draft, buyer: next })}
            getKey={(item) => item.id}
            getLabel={(item) => (item.partner_code ? `${item.name_ko} (${item.partner_code})` : item.name_ko)}
          />
        </div>
        {canBulk && (
          <div className="flex w-48 flex-col gap-1">
            <span className="cell-nowrap text-gray-600">담당자</span>
            <SearchSelect<UserLookup>
              label="담당자"
              path="/v1/users/lookup"
              params={{ assignee_target: "true" }}
              queryKey={["users", "lookup"]}
              value={draft.assignee}
              onChange={(next) => setDraft({ ...draft, assignee: next })}
              getKey={(item) => item.id}
              getLabel={(item) => item.display_name}
            />
          </div>
        )}
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">통화</span>
            <select
              name="currency"
              value={draft.currency}
              onChange={(event) => setDraft({ ...draft, currency: event.target.value })}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">전체</option>
              {(currencies.data?.items ?? []).map((currency) => (
                <option key={currency.code} value={currency.code}>
                  {currency.code}
                </option>
              ))}
            </select>
          </label>
          <FieldError id="board-filter-currency-error" message={fieldErrors.currency} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">목적 시장</span>
            <select
              name="dest_market_code"
              value={draft.dest_market_code}
              onChange={(event) => setDraft({ ...draft, dest_market_code: event.target.value })}
              className="rounded border border-gray-300 px-3 py-2"
            >
              <option value="">전체</option>
              {(markets.data?.items ?? []).map((market) => (
                <option key={market.code} value={market.code}>
                  {market.code} {market.name_ko}
                </option>
              ))}
            </select>
          </label>
          <FieldError id="board-filter-dest_market_code-error" message={fieldErrors.dest_market_code} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">접수일 시작</span>
            <input
              type="date"
              name="created_from"
              min={FILTER_DATE_MIN}
              max={FILTER_DATE_MAX}
              value={draft.created_from}
              aria-invalid={fieldErrors.created_from !== undefined || fieldErrors.created_range !== undefined}
              aria-describedby={fieldErrors.created_from ? "board-filter-created_from-error" : undefined}
              onChange={(event) => setDraft({ ...draft, created_from: event.target.value })}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <FieldError id="board-filter-created_from-error" message={fieldErrors.created_from} />
        </div>
        <div className="flex flex-col gap-1">
          <label className="flex flex-col gap-1">
            <span className="cell-nowrap text-gray-600">접수일 끝</span>
            <input
              type="date"
              name="created_to"
              min={FILTER_DATE_MIN}
              max={FILTER_DATE_MAX}
              value={draft.created_to}
              aria-invalid={fieldErrors.created_to !== undefined || fieldErrors.created_range !== undefined}
              aria-describedby={fieldErrors.created_to || fieldErrors.created_range ? "board-filter-created_to-error" : undefined}
              onChange={(event) => setDraft({ ...draft, created_to: event.target.value })}
              className="rounded border border-gray-300 px-3 py-2"
            />
          </label>
          <FieldError id="board-filter-created_to-error" message={fieldErrors.created_to ?? fieldErrors.created_range} />
        </div>
        <div className="flex gap-2 self-end">
          <button type="submit" className="cell-nowrap rounded bg-gray-900 px-4 py-2 text-white">
            조회
          </button>
          <button type="button" onClick={resetFilter} className="cell-nowrap rounded border border-gray-300 px-3 py-2">
            조건 초기화
          </button>
        </div>
      </form>
      <p className="mt-1 break-keep text-xs text-gray-500">접수일은 한국 시간(KST) 날짜 기준이며 시작·끝 날짜를 모두 포함합니다.</p>
      <FieldError message={fieldErrors.form} />

      <BoardSavedFilters current={applied} onApply={applySaved} />

      {canBulk && (
        <section aria-label="벌크 처리" className="mt-3 flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 p-3 text-sm">
          <span className="cell-nowrap">
            선택 <span className="num">{selectedCards.length}</span>건
          </span>
          <button
            type="button"
            disabled={selectedCards.length === 0}
            onClick={() => setSelected(new Map())}
            className="cell-nowrap text-gray-500 underline disabled:opacity-40"
          >
            선택 해제
          </button>
          <button
            type="button"
            disabled={intakeCount === 0 || tooMany(intakeCount) || bulk.isPending}
            onClick={() => setPendingAction("CONFIRM_INTAKE")}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1.5 disabled:opacity-40"
          >
            인테이크 확정 ({intakeCount}건)
          </button>
          <button
            type="button"
            disabled={soCount === 0 || tooMany(soCount) || bulk.isPending}
            onClick={() => setPendingAction("CONFIRM_SO")}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1.5 disabled:opacity-40"
          >
            수주 확정 ({soCount}건)
          </button>
          <div className="w-48">
            <SearchSelect<UserLookup>
              label="지정할 담당자"
              path="/v1/users/lookup"
              params={{ assignee_target: "true" }}
              queryKey={["users", "lookup"]}
              value={assignee}
              onChange={setAssignee}
              getKey={(item) => item.id}
              getLabel={(item) => item.display_name}
              placeholder="지정할 담당자 검색"
            />
          </div>
          <button
            type="button"
            disabled={selectedCards.length === 0 || assignee === null || tooMany(selectedCards.length) || bulk.isPending}
            onClick={() => setPendingAction("ASSIGN")}
            className="cell-nowrap rounded border border-gray-300 px-3 py-1.5 disabled:opacity-40"
          >
            담당자 지정 ({selectedCards.length}건)
          </button>
          {(tooMany(intakeCount) || tooMany(soCount) || tooMany(selectedCards.length)) && (
            <p role="status" className="w-full break-keep text-xs text-signal-red">
              한 번에 {BULK_MAX_TARGETS}건까지 처리할 수 있습니다. 선택을 나눠서 처리해 주세요.
            </p>
          )}
        </section>
      )}

      {lost && attempt !== null && !dialogOpen && (
        <div role="alert" className="mt-3 flex flex-wrap items-center gap-3 rounded border border-signal-amber p-3 text-sm">
          <span className="break-keep">
            {ACTION_LABEL[attempt.request.action]} 요청의 응답을 받지 못했습니다. 서버에서 처리되었을 수 있습니다 — 같은 요청으로 결과를 다시 받아 확인해 주세요.
          </span>
          <button type="button" onClick={resend} disabled={bulk.isPending} className="cell-nowrap rounded bg-gray-900 px-3 py-1.5 text-white disabled:opacity-50">
            결과 다시 받기
          </button>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3 text-xs text-gray-500">
        {board.data && <span className="cell-nowrap">기준 시각 {toKstDisplay(board.data.generated_at)}</span>}
        <button type="button" onClick={refreshBoard} className="cell-nowrap underline">
          보드 새로 고침
        </button>
      </div>

      <ListState
        isPending={board.isPending}
        error={board.error}
        isEmpty={false}
        emptyHint=""
      >
        <div className="mt-2 overflow-x-auto pb-2">
          <div className="flex gap-3">
            {board.data?.columns.map((column) => (
              <BoardColumnView
                key={column.stage}
                column={column}
                selectable={canBulk}
                isSelected={isSelected}
                onToggle={toggle}
                onSelectAll={selectAll}
                onMore={(stage, label) => setDrill({ stage, label })}
              />
            ))}
          </div>
        </div>
      </ListState>

      {drill && (
        <BoardDrilldown
          stage={drill.stage}
          label={drill.label}
          filter={applied}
          selectable={canBulk}
          isSelected={isSelected}
          onToggle={toggle}
          onClose={() => setDrill(null)}
        />
      )}

      {pendingAction !== null && (
        <ConfirmDialog
          title={`${ACTION_LABEL[pendingAction]} — ${confirmCount}건`}
          description={
            <>
              <p>
                선택한 {confirmCount}건을 {pendingAction === "ASSIGN" ? `'${assignee?.display_name ?? ""}' 담당으로 지정합니다` : "확정합니다"}. 건마다 따로
                처리되며, 막히거나 먼저 바뀐 건은 결과에 따로 표시됩니다.
              </p>
              {pendingAction !== "ASSIGN" && (
                <p className="mt-1">승인·예외 승인은 벌크로 부여되지 않습니다 — 막힌 건은 결과의 상세 링크에서 개별 처리합니다.</p>
              )}
              {skippedKinds > 0 && <p className="mt-1">선택 중 이 동작의 대상이 아닌 {skippedKinds}건은 보내지 않습니다.</p>}
            </>
          }
          confirmLabel={ACTION_LABEL[pendingAction]}
          pending={bulk.isPending}
          onConfirm={() => run(pendingAction)}
          onCancel={() => setPendingAction(null)}
        />
      )}

      {dialogOpen && attempt !== null && (
        <BulkResultDialog
          action={attempt.request.action}
          report={report}
          error={bulkError}
          canResend={lost}
          pending={bulk.isPending}
          labels={attempt.labels}
          onResend={resend}
          onRefreshBoard={() => {
            refreshBoard();
            closeResult();
          }}
          onClose={closeResult}
        />
      )}
    </section>
  );
}
