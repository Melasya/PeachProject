import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { HealthBadge } from "@/components/health-badge";
import { api } from "@/lib/api";
import { renderWithQuery } from "./utils";

describe("HealthBadge", () => {
  it("shows Connected when the database answers", async () => {
    vi.spyOn(api, "readiness").mockResolvedValue({
      status: "ok",
      database: "ok",
    });
    renderWithQuery(<HealthBadge />);
    await waitFor(() =>
      expect(screen.getByText("Connected")).toBeInTheDocument(),
    );
  });

  it("shows Unavailable when the database does not answer", async () => {
    vi.spyOn(api, "readiness").mockResolvedValue({
      status: "ok",
      database: "down",
    });
    renderWithQuery(<HealthBadge />);
    await waitFor(() =>
      expect(screen.getByText("Unavailable")).toBeInTheDocument(),
    );
  });

  it("shows Unavailable when the request fails", async () => {
    vi.spyOn(api, "readiness").mockImplementation(async () => {
      throw new Error("boom");
    });
    renderWithQuery(<HealthBadge />);
    await waitFor(() =>
      expect(screen.getByText("Unavailable")).toBeInTheDocument(),
    );
  });
});
