// 로그인 후 공통 껍데기 — 상단 네비 + 사용자 + 로그아웃 (DESIGN.md §14).

import { NavLink, Outlet } from "react-router";
import { useLogout, useSession } from "../lib/session";

const NAV = [
  { to: "/skus", label: "SKU" },
  { to: "/products", label: "제품(처방)" },
  { to: "/ingredients", label: "성분" },
  { to: "/materials", label: "자재" },
  { to: "/markets", label: "시장" },
  { to: "/requirement-templates", label: "요건 템플릿" },
  { to: "/partners", label: "거래처" },
  { to: "/documents", label: "문서보관소" },
  { to: "/imports", label: "엑셀 임포트" },
  { to: "/brands", label: "브랜드" },
  { to: "/item-profiles", label: "품목군" },
] as const;

export function AppShell() {
  const { me } = useSession();
  const logout = useLogout();

  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-200">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 p-4">
          <span className="cell-nowrap font-bold">K-Beauty Trade OS</span>
          <nav className="flex gap-4 text-sm">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `cell-nowrap ${isActive ? "font-semibold text-gray-900 underline" : "text-gray-500"}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
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
