import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { queryClient } from "./lib/queryClient";
import { useSession } from "./lib/session";
import { AgenciesPage } from "./routes/agencies";
import { MarketWizardPage } from "./routes/market-wizard";
import { AlertsPage } from "./routes/alerts";
import { ApprovalDetailPage } from "./routes/approval-detail";
import { ApprovalLinesPage } from "./routes/approval-lines";
import { ApprovalsPage } from "./routes/approvals";
import { DelegationsPage } from "./routes/delegations";
import { BrandsPage } from "./routes/brands";
import { DocumentsPage } from "./routes/documents";
import { HealthPage } from "./routes/health";
import { HolidaysPage } from "./routes/holidays";
import { ImportsPage } from "./routes/imports";
import { IngredientDetailPage } from "./routes/ingredient-detail";
import { IngredientsPage } from "./routes/ingredients";
import { ItemProfilesPage } from "./routes/item-profiles";
import { LoginPage } from "./routes/login";
import { MarketsPage } from "./routes/markets";
import { CertificationsPage } from "./routes/certifications";
import { CertificationBoardPage } from "./routes/certification-board";
import { ReadinessPage } from "./routes/readiness";
import { RequirementTemplatesPage } from "./routes/requirement-templates";
import { MaterialsPage } from "./routes/materials";
import { NotFoundPage } from "./routes/not-found";
import { PoliciesPage } from "./routes/policies";
import { SettingsUsersPage } from "./routes/settings-users";
import { PartnersPage } from "./routes/partners";
import { BankAccountsPage } from "./routes/bank-accounts";
import { ProformaDetailPage } from "./routes/proforma-detail";
import { ProformaListPage } from "./routes/proformas";
import { QuotationDetailPage } from "./routes/quotation-detail";
import { QuotationListPage } from "./routes/quotations";
import { OrderBoardPage } from "./routes/order-board";
import { OrderIntakeCreatePage } from "./routes/order-intake-create";
import { OrderIntakeDetailPage } from "./routes/order-intake-detail";
import { OrderIntakeListPage } from "./routes/order-intakes";
import { PurchaseOrderCreatePage } from "./routes/purchase-order-create";
import { PurchaseOrderDetailPage } from "./routes/purchase-order-detail";
import { PurchaseOrderListPage } from "./routes/purchase-orders";
import { SalesOrderDetailPage } from "./routes/sales-order-detail";
import { SalesOrderListPage } from "./routes/sales-orders";
import { ShipmentDetailPage } from "./routes/shipment-detail";
import { ShipmentListPage } from "./routes/shipments";
import { ProductDetailPage } from "./routes/product-detail";
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
        <Route path="/products/:productId" element={<ProductDetailPage />} />
        <Route path="/ingredients" element={<IngredientsPage />} />
        <Route path="/ingredients/:ingredientId" element={<IngredientDetailPage />} />
        <Route path="/materials" element={<MaterialsPage />} />
        <Route path="/markets" element={<MarketsPage />} />
        <Route path="/requirement-templates" element={<RequirementTemplatesPage />} />
        <Route path="/readiness" element={<ReadinessPage />} />
        <Route path="/certifications" element={<CertificationsPage />} />
        <Route path="/certification-board" element={<CertificationBoardPage />} />
        <Route path="/agencies" element={<AgenciesPage />} />
        <Route path="/market-wizard" element={<MarketWizardPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/partners" element={<PartnersPage />} />
        <Route path="/quotations" element={<QuotationListPage />} />
        <Route path="/quotations/:quotationId" element={<QuotationDetailPage />} />
        <Route path="/proforma-invoices" element={<ProformaListPage />} />
        <Route path="/proforma-invoices/:proformaId" element={<ProformaDetailPage />} />
        <Route path="/sales-orders" element={<SalesOrderListPage />} />
        <Route path="/sales-orders/:salesOrderId" element={<SalesOrderDetailPage />} />
        <Route path="/orders/board" element={<OrderBoardPage />} />
        <Route path="/orders/intakes" element={<OrderIntakeListPage />} />
        <Route path="/orders/intakes/new" element={<OrderIntakeCreatePage />} />
        <Route path="/orders/intakes/:intakeId" element={<OrderIntakeDetailPage />} />
        <Route path="/purchase-orders" element={<PurchaseOrderListPage />} />
        <Route path="/purchase-orders/new" element={<PurchaseOrderCreatePage />} />
        <Route path="/purchase-orders/:purchaseOrderId" element={<PurchaseOrderDetailPage />} />
        {/* 선적 — 생성은 수주 상세의 '선적 만들기'(SO 참조 2단)뿐, /shipments/new 독립 생성 화면은 두지 않는다(design-D D5). */}
        <Route path="/shipments" element={<ShipmentListPage />} />
        <Route path="/shipments/:shipmentId" element={<ShipmentDetailPage />} />
        <Route path="/holidays" element={<HolidaysPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/approvals/:approvalId" element={<ApprovalDetailPage />} />
        <Route path="/approval-lines" element={<ApprovalLinesPage />} />
        <Route path="/delegations" element={<DelegationsPage />} />
        <Route path="/bank-accounts" element={<BankAccountsPage />} />
        <Route path="/documents" element={<DocumentsPage />} />
        <Route path="/imports" element={<ImportsPage />} />
        <Route path="/brands" element={<BrandsPage />} />
        <Route path="/item-profiles" element={<ItemProfilesPage />} />
        <Route path="/settings/policies" element={<PoliciesPage />} />
        <Route path="/settings/users" element={<SettingsUsersPage />} />
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
