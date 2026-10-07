"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";
import { hasAuthParams, useAuth } from "react-oidc-context";

import { PageHeader } from "@/components/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { authConfigured } from "@/lib/auth";

/* The URL to share: opening it sends the browser to Cognito's managed login
 * (email + password, sign-up, Continue with Google). It is also the callback -
 * Cognito returns here with ?code=..., which the AuthProvider exchanges. The
 * login has to start in the app, so the PKCE verifier and state it keeps match
 * what comes back. */
export default function LoginPage() {
  const auth = useAuth();
  const router = useRouter();
  // React runs effects twice in development; redirect only once.
  const started = useRef(false);

  useEffect(() => {
    if (!authConfigured || started.current) return;
    // Coming back from Cognito: the AuthProvider is handling the code.
    if (hasAuthParams()) return;
    if (auth.isLoading || auth.activeNavigator || auth.error) return;
    if (auth.isAuthenticated) {
      router.replace("/");
      return;
    }
    started.current = true;
    void auth.signinRedirect();
  }, [auth, router]);

  if (!authConfigured) {
    return (
      <Alert>
        <AlertTitle>Sign-in is not configured</AlertTitle>
        <AlertDescription>
          This build has no Cognito settings. Run make deploy-auth, then rebuild
          the frontend.
        </AlertDescription>
      </Alert>
    );
  }

  if (auth.error) {
    return (
      <div className="grid gap-6">
        <Alert variant="destructive">
          <AlertTitle>Sign-in failed</AlertTitle>
          <AlertDescription>{auth.error.message}</AlertDescription>
        </Alert>
        <Button
          variant="outline"
          className="justify-self-start"
          onClick={() => {
            started.current = true;
            void auth.signinRedirect();
          }}
        >
          Try again
        </Button>
      </div>
    );
  }

  return (
    <PageHeader
      icon="🔑"
      title="Signing in"
      description="Taking you to the sign-in page..."
    />
  );
}
