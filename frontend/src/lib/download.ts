// 내려받은 Blob을 파일로 저장 (apiDownload와 한 쌍 — 에러는 화면이 한국어로 보인다).

import { apiDownload } from "./api";

export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const { blob, filename } = await apiDownload(path, fallbackName);
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  // 즉시 해제하면 일부 브라우저에서 저장이 시작되기 전에 주소가 사라진다.
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
