import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { queryClient } from "./lib/queryClient";
import { useSession } from "./lib/session";
import { BrandsPage } from "./routes/brands";
import { HealthPage } from "./routes/health";
import { ItemProfilesPage } from "./routes/item-profiles";
import { LoginPage } from "./routes/login";
import { NotFoundPage } from "./routes/not-found";
import { ProductsPage } from "./routes/products";
import { AppShell } from "./routes/shell";
import { SkuDetailPage } from "./routes/sku-detail";
import { SkuListPage } from "./routes/skus";

/**
 * 로그인한 사람만 지나갈 수 있는 관문.
 *
 * ★ 여기서 막는 것은 **표시**일 뿐 보안이 아니다. 실제 차단은 서버가 한다
 *   (§18.1 "인가는 API에서 강제"). 이 가드만 믿으면 브라우저 개발자 도구로
 *   상태를 바꾸거나 API를 직접 부르는 것만으로 뚫린다. 화면 게이트의 목적은
 *   "권한 없는 사람이 빈 화면과 401을 마주치지 않게" 하는 것뿐이다.
 */
function RequireSession() {
  const { me, isPending, unauthenticated, error } = useSession();

  if (isPending) {
    return <p className="p-8 text-gray-500">확인 중…</p>;
  }

  // 서버가 안 뜬 상태와 로그인 안 한 상태는 다르다. 전자는 헬스 화면이
  // 원인과 조치를 알려 준다(비개발자의 첫 실행에서 가장 흔한 상태다).
  if (error) {
    return <HealthPage />;
  }

  if (unauthenticated || !me) {
    return <Navigate to="/login" replace />;
  }

  return <AppShell />;
}

/** 이미 로그인한 사람이 /login으로 오면 목록으로 돌려보낸다. */
function LoginGate() {
  const { me, isPending } = useSession();

  if (isPending) {
    return <p className="p-8 text-gray-500">확인 중…</p>;
  }
  if (me) {
    return <Navigate to="/skus" replace />;
  }
  return <LoginPage />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginGate />} />
      {/* 헬스 화면은 로그인 없이도 열려야 한다 — 서버가 죽었을 때 원인을 보는 곳이다. */}
      <Route path="/health" element={<HealthPage />} />

      <Route element={<RequireSession />}>
        <Route index element={<Navigate to="/skus" replace />} />
        <Route path="/skus" element={<SkuListPage />} />
        <Route path="/skus/:skuId" element={<SkuDetailPage />} />
        <Route path="/products" element={<ProductsPage />} />
        <Route path="/brands" element={<BrandsPage />} />
        <Route path="/item-profiles" element={<ItemProfilesPage />} />
      </Route>

      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
