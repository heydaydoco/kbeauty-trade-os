import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./lib/queryClient";
import { HealthPage } from "./routes/health";

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <HealthPage />
    </QueryClientProvider>
  );
}
