/// <reference types="node" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Tailwind v4는 CSS-first다 — tailwind.config.js가 없다. 설정은 src/styles/theme.css의
// @theme 블록에 있다. (CI가 tailwind.config.* 부재를 검사한다)
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true, // 컨테이너 밖(호스트 브라우저)에서 접속 가능하게
    port: 5173,
    // Windows 바인드마운트는 파일 변경 이벤트가 전달되지 않아 HMR이 죽는다.
    // 환경변수로 폴링을 켠다(compose가 VITE_USE_POLLING=true를 준다).
    watch: process.env.VITE_USE_POLLING ? { usePolling: true } : undefined,
    proxy: {
      // 프런트는 백엔드 주소를 모른다. 상대경로 /api를 vite가 백엔드로 넘긴다.
      // 운영에서는 nginx가 같은 역할을 한다(동일 오리진 → CORS 불필요).
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://api:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
