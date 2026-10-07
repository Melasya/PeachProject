"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "react-oidc-context";

import { Button } from "@/components/ui/button";
import { authConfigured, signOut } from "@/lib/auth";
import { cn } from "@/lib/utils";

const links = [
  { href: "/", label: "Dashboard" },
  { href: "/items", label: "Board" },
];

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-40 border-b border-border/70 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-14 w-full max-w-5xl items-center gap-8 px-6 sm:px-8">
        <Link href="/" className="flex items-center gap-2">
          <span
            aria-hidden
            className="grid size-6 place-items-center rounded-md bg-foreground font-heading text-[13px] leading-none font-semibold text-background"
          >
            P
          </span>
          <span className="font-heading text-[15px] font-semibold tracking-tight">
            Peach
          </span>
        </Link>

        <nav className="flex items-center gap-1 text-sm">
          {links.map((link) => {
            const active =
              link.href === "/"
                ? pathname === "/"
                : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "rounded-md px-2.5 py-1.5 font-medium transition-colors",
                  active
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        {authConfigured && <AccountMenu />}
      </div>
    </header>
  );
}

/** Sign in when signed out; the user's email and Sign out when signed in. */
function AccountMenu() {
  const auth = useAuth();

  // Nothing until the stored session has been read, so the header does not
  // flash "Sign in" for someone who is signed in.
  if (auth.isLoading) return null;

  if (auth.isAuthenticated) {
    return (
      <div className="ml-auto flex min-w-0 items-center gap-3 text-sm">
        <span className="truncate text-muted-foreground">
          {auth.user?.profile.email}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void signOut(() => auth.removeUser())}
        >
          Sign out
        </Button>
      </div>
    );
  }

  return (
    <Button
      size="sm"
      className="ml-auto"
      onClick={() => void auth.signinRedirect()}
    >
      Sign in
    </Button>
  );
}
