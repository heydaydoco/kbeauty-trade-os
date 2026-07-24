import js from "@eslint/js";
import globals from "globals";

// 최소 골격. 규칙 강화는 화면이 늘어나는 Phase 1 이후에 재검토한다.
export default [
  { ignores: ["dist", "coverage", "node_modules"] },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2023,
      globals: { ...globals.browser },
    },
  },
];
