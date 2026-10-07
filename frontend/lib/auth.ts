import { WebStorageStateStore } from "oidc-client-ts";
import type { AuthProviderProps } from "react-oidc-context";

/* Cognito settings, compiled in at build time like NEXT_PUBLIC_API_URL:
 * scripts/deploy-frontend.sh reads them from the auth stack's outputs. All
 * three are public - the app client has no secret, and the Google client
 * secret lives only in Cognito. */
const authority = process.env.NEXT_PUBLIC_COGNITO_AUTHORITY ?? "";
const clientId = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID ?? "";
const domain = (process.env.NEXT_PUBLIC_COGNITO_DOMAIN ?? "").replace(
  /\/$/,
  "",
);

/** False in a build without the auth stack's values: sign-in is hidden. */
export const authConfigured = Boolean(authority && clientId && domain);

// The static export prerenders without a browser; window only exists client-side.
const origin = typeof window === "undefined" ? "" : window.location.origin;

/** The page that starts sign-in and receives Cognito's redirect back. Must
 *  match the client's CallbackURLs exactly, trailing slash included. */
export const loginPath = "/login/";

export const oidcConfig: AuthProviderProps = {
  authority,
  client_id: clientId,
  redirect_uri: `${origin}${loginPath}`,
  // Authorization code flow; oidc-client-ts adds PKCE on its own.
  response_type: "code",
  scope: "openid email profile",
  // localStorage rather than the default sessionStorage, so a new tab is
  // still signed in.
  ...(typeof window === "undefined"
    ? {}
    : { userStore: new WebStorageStateStore({ store: window.localStorage }) }),
};

/** Cognito has no OIDC end-session endpoint: drop the local session, then
 *  send the browser to Cognito's own /logout, which returns to the homepage. */
export async function signOut(removeUser: () => Promise<void>): Promise<void> {
  await removeUser();
  const url = new URL("/logout", domain);
  url.searchParams.set("client_id", clientId);
  url.searchParams.set("logout_uri", `${window.location.origin}/`);
  window.location.assign(url.toString());
}
