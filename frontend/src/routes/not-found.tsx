// 없는 주소 (DESIGN.md §18.4 — 오류 메시지는 원인과 조치를 함께 준다).

import { Link } from "react-router";

export function NotFoundPage() {
  return (
    <main className="mx-auto max-w-lg p-8">
      <h1 className="text-2xl font-bold">주소를 찾을 수 없습니다</h1>
      <p className="mt-2 text-sm text-gray-500">
        입력한 주소에 해당하는 화면이 없습니다. 즐겨찾기가 오래됐거나 주소가 잘못 입력됐을 수
        있습니다.
      </p>
      <Link to="/skus" className="mt-4 inline-block text-sm text-gray-700 underline">
        SKU 목록으로 가기
      </Link>
    </main>
  );
}
