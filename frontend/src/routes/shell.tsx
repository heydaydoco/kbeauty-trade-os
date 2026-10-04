// 로그인 후 공통 껍데기 — 상단 네비 + 사용자 + 로그아웃 (DESIGN.md §14).

import { NavLink, Outlet } from "react-router";
import { apiFetch } from "../lib/api";
import { hasRole, useLogout, useSession } from "../lib/session";
import { useQuery } from "@tanstack/react-query";

const NAV = [
  { to: "/readiness", label: "시장 준비도" },
  { to: "/skus", label: "SKU" },
  { to: "/products", label: "제품(처방)" },
  { to: "/ingredients", label: "성분" },
  { to: "/materials", label: "자재" },
  { to: "/markets", label: "시장" },
  { to: "/market-wizard", label: "신규 시장 개설" },
  { to: "/requirement-templates", label: "요건 템플릿" },
  { to: "/certifications", label: "인증" },
  { to: "/certification-board", label: "인증 보드" },
  { to: "/agencies", label: "대행사" },
  { to: "/partners", label: "거래처" },
  { to: "/quotations", label: "견적" },
  { to: "/proforma-invoices", label: "PI" },
  { to: "/orders/board", label: "오더 보드" },
  { to: "/orders/intakes", label: "주문 접수(인테이크)" },
  { to: "/sales-orders", label: "수주" },
  { to: "/purchase-orders", label: "발주" },
  // 휴일 캘린더 — 열람은 전 역할(design-D §D5: "선적" 다음 자리 — 선적 메뉴가 생기면 그 뒤에 둔다).
  { to: "/holidays", label: "휴일 캘린더" },
  { to: "/documents", label: "문서보관소" },
  { to: "/imports", label: "엑셀 임포트" },
  { to: "/brands", label: "브랜드" },
  { to: "/item-profiles", label: "품목군" },
] as const;

/** ADMIN에게만 보이는 메뉴 — 표시 편의일 뿐, 서버가 정본이다(§18.1). */
const ADMIN_NAV = [
  { to: "/settings/policies", label: "정책 설정" },
  { to: "/settings/users", label: "사용자·역할" },
] as const;

/** 은행 계좌 — 백엔드 authz_matrix: 조회 ADMIN·TRADE(쓰기는 ADMIN 전용이며 화면 안에서 다시 가른다). 표시 편의일 뿐 서버가 정본. */
const BANK_NAV = { to: "/bank-accounts", label: "은행 계좌" } as const;

/** 승인 메뉴 — 백엔드 authz_matrix: 비조회 4역할(VIEWER 전면 403). 표시 편의일 뿐 서버가 정본이다(§18.1). */
const APPROVAL_NAV = [
  { to: "/approvals", label: "결재함" },
  { to: "/approval-lines", label: "결재선" },
  { to: "/delegations", label: "대결" },
] as const;

/** 결재함 대기 건수 — 알림과 무관한 서버 계산(알림이 늦거나 없어도 뜬다). 접두 키 ["approvals"]라 결정 뒤 같이 갱신된다. */
export const INBOX_COUNT_QUERY_KEY = ["approvals", "inbox-count"] as const;

/** 미확인 알림 수 — 셸에 상시 노출한다(알림센터를 열어야만 아는 알림은 안 읽힌다). */
export const UNREAD_QUERY_KEY = ["alerts", "unread-count"] as const;

export function AppShell() {
  const { me } = useSession();
  const logout = useLogout();
  const unread = useQuery({
    queryKey: UNREAD_QUERY_KEY,
    queryFn: () => apiFetch<{ count: number }>("/v1/alerts/unread-count"),
  });

  const canApprove = hasRole(me, "TRADE", "LOGISTICS", "CERT");
  // 조회 전용 역할은 호출하지 않는다(403 소음 방지). 조회 실패는 배지를 숨긴다 — 배지 하나로 셸이 깨지지 않는다.
  const inbox = useQuery({
    queryKey: INBOX_COUNT_QUERY_KEY,
    queryFn: () => apiFetch<{ count: number }>("/v1/approvals/inbox-count"),
    enabled: canApprove,
    staleTime: 0,
  });
  const inboxCount = typeof inbox.data?.count === "number" ? inbox.data.count : 0;

  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-200">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 p-4">
          <span className="cell-nowrap font-bold">K-Beauty Trade OS</span>
          <nav className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {[
              ...NAV,
              ...(canApprove ? APPROVAL_NAV : []),
              ...(hasRole(me, "TRADE") ? [BANK_NAV] : []),
              ...(hasRole(me) ? ADMIN_NAV : []),
            ].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                title={item.to === "/approvals" && inbox.isError ? "결재 대기 건수를 불러오지 못했습니다(0건이 아닙니다)" : undefined}
                className={({ isActive }) =>
                  `cell-nowrap ${isActive ? "font-semibold text-gray-900 underline" : "text-gray-500"}`
                }
              >
                {item.label}
                {item.to === "/approvals" && inbox.isError ? " !" : ""}
                {item.to === "/approvals" && !inbox.isError && inboxCount > 0 ? ` ${inboxCount}` : ""}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <NavLink
              to="/alerts"
              className={({ isActive }) =>
                `cell-nowrap ${isActive ? "font-semibold text-gray-900 underline" : "text-gray-500"}`
              }
            >
              {/* 조회 실패는 배지를 숨긴다 — 알림 수 하나 때문에 셸이 깨지지 않는다. */}
              알림{unread.data && unread.data.count > 0 ? ` ${unread.data.count}` : ""}
            </NavLink>
            <span className="cell-nowrap text-gray-500">
              {me?.display_name} ({me?.roles.join(", ") || "역할 없음"})
            </span>
            <button
              type="button"
              onClick={() => logout.mutate()}
              className="cell-nowrap text-gray-500 underline"
            >
              로그아웃
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl p-8">
        <Outlet />
      </main>
    </div>
  );
}
