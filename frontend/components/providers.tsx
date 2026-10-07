"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthProvider } from "react-oidc-context";

import { oidcConfig } from "@/lib/auth";

export function Providers({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
        },
      }),
  );

  return (
    <AuthProvider
      {...oidcConfig}
      // Back from Cognito with ?code=...: the provider has exchanged it for
      // tokens; drop the code from the address bar and go home.
      onSigninCallback={() => router.replace("/")}
    >
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </AuthProvider>
  );
}
