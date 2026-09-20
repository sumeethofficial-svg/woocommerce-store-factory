import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { Store, StoreStatus, Usage } from "./types";

const user = { id: 1, username: "demo_user", email: "demo@example.com" };

const makeStore = (status: StoreStatus): Store => ({
  id: 1,
  name: "demo",
  owner: "demo_user",
  namespace: "store-demo",
  status,
  storage_gi: 3,
  url: status === "ready" ? "http://demo.localhost" : null,
  admin_url: status === "ready" ? "http://demo.localhost/wp-admin/" : null,
  error: null,
  created_at: "2026-09-19T10:00:00Z",
  updated_at: "2026-09-19T10:00:00Z",
});

const json = (body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));

let usage: Usage;
let listCalls: number;
let deleteCalls: number;
let credentialCalls: number;
let statuses: StoreStatus[];
let deleted: boolean;

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.setItem("store-factory.token", "token");
  listCalls = 0;
  deleteCalls = 0;
  credentialCalls = 0;
  deleted = false;
  statuses = ["provisioning", "initializing", "ready"];
  usage = {
    stores: { used: 2, limit: 3 },
    storage_gi: { used: 5, limit: 5 },
    max_store_storage_gi: 5,
    mysql_storage_gi: 1,
    online_payments: true,
  };

  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return json(user);
      if (url.endsWith("/api/stores/usage")) return json(usage);
      if (url.endsWith("/api/stores/1/credentials")) {
        credentialCalls += 1;
        return json({ username: "admin", password: "Wgr^3czxSs$U*F*@", admin_url: null });
      }
      if (url.endsWith("/api/stores/1") && init?.method === "DELETE") {
        deleteCalls += 1;
        deleted = true;
        return json(makeStore("deleting"));
      }
      if (url.endsWith("/api/stores/1/events")) return json([]);
      if (url.endsWith("/api/stores")) {
        listCalls += 1;
        if (deleted) return json([]);
        const status = statuses[Math.min(listCalls - 1, statuses.length - 1)];
        return json([makeStore(status)]);
      }
      return Promise.resolve(new Response("{}", { status: 404 }));
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
});

const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

describe("dashboard polling", () => {
  it("follows the store from provisioning to ready and then slows down", async () => {
    render(<App />);
    await advance(0);
    expect(screen.getAllByText("Provisioning").length).toBeGreaterThan(0);
    expect(screen.queryByRole("link", { name: "http://demo.localhost" })).toBeNull();

    await advance(3000);
    expect(screen.getAllByText("Initializing").length).toBeGreaterThan(0);

    await advance(3000);
    const link = screen.getByRole("link", { name: "http://demo.localhost" }) as HTMLAnchorElement;
    expect(link.href).toBe("http://demo.localhost/");
    expect(screen.getByRole("link", { name: "http://demo.localhost/wp-admin/" })).toBeTruthy();

    await advance(0);
    const callsWhenReady = listCalls;
    await advance(15000);
    expect(listCalls).toBe(callsWhenReady);
    await advance(6000);
    expect(listCalls).toBe(callsWhenReady + 1);
  });

  it("deletes a store after confirmation and shows the empty state", async () => {
    statuses = ["ready"];
    render(<App />);
    await advance(0);

    fireEvent.click(screen.getByText("Delete"));
    expect(deleteCalls).toBe(0);
    fireEvent.click(screen.getByText("Delete permanently"));
    await advance(0);
    expect(deleteCalls).toBe(1);

    await advance(3000);
    expect(screen.getByText(/No stores yet/)).toBeTruthy();
  });

  it("shows the sign in form without a token", async () => {
    localStorage.clear();
    render(<App />);
    await advance(0);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.getByLabelText("Username")).toBeTruthy();
    expect(listCalls).toBe(0);
  });
});

describe("resource summary and store card", () => {
  it("shows usage and the signed in user from backend data", async () => {
    render(<App />);
    await advance(0);
    expect(screen.getByText("2 / 3")).toBeTruthy();
    expect(screen.getByText("5 / 5 Gi")).toBeTruthy();
    expect(screen.getByText("demo_user", { selector: ".stat-user span" })).toBeTruthy();
    expect(screen.getByText("Sign out")).toBeTruthy();
  });

  it("disables New store when the storage quota is used up", async () => {
    render(<App />);
    await advance(0);
    expect((screen.getByText("New store") as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables New store and opens the launch dialog when there is room", async () => {
    usage.storage_gi.used = 3;
    render(<App />);
    await advance(0);
    const button = screen.getByText("New store") as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("shows owner, namespace, storage and creation date on the card", async () => {
    statuses = ["ready"];
    render(<App />);
    await advance(0);
    expect(screen.getByText("store-demo")).toBeTruthy();
    expect(screen.getByText("3 Gi")).toBeTruthy();
    expect(screen.getAllByText("demo_user").length).toBeGreaterThan(1);
  });

  it("loads credentials only for a ready store and keeps the password hidden until asked", async () => {
    statuses = ["provisioning"];
    render(<App />);
    await advance(0);
    expect(credentialCalls).toBe(0);
    expect(screen.getByText("Available once the store is ready.")).toBeTruthy();

    cleanup();
    statuses = ["ready"];
    listCalls = 0;
    render(<App />);
    await advance(0);
    expect(credentialCalls).toBe(1);
    expect(screen.getByText("admin")).toBeTruthy();
    expect(screen.queryByText("Wgr^3czxSs$U*F*@")).toBeNull();
    fireEvent.click(screen.getByText("Show"));
    expect(screen.getByText("Wgr^3czxSs$U*F*@")).toBeTruthy();
  });
});
