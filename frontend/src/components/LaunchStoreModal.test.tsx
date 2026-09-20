import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Usage } from "../types";
import LaunchStoreModal, { generatePassword } from "./LaunchStoreModal";

let usage: Usage;
let posted: Record<string, unknown> | null;
let response: Response;
const onClose = vi.fn();
const onCreated = vi.fn();

beforeEach(() => {
  usage = {
    stores: { used: 1, limit: 3 },
    storage_gi: { used: 2, limit: 5 },
    max_store_storage_gi: 5,
    mysql_storage_gi: 1,
    online_payments: true,
  };
  posted = null;
  response = new Response(JSON.stringify({ id: 1 }), { status: 201 });
  onClose.mockReset();
  onCreated.mockReset();
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
      posted = JSON.parse(String(init?.body));
      return Promise.resolve(response);
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const renderModal = () => render(<LaunchStoreModal usage={usage} onClose={onClose} onCreated={onCreated} />);
const launchButton = () => screen.getByRole("button", { name: "Launch store" }) as HTMLButtonElement;
const type = (element: HTMLElement, value: string) => fireEvent.change(element, { target: { value } });
const nameInput = () => screen.getByPlaceholderText("my-shop");
const passwordInput = () => screen.getByLabelText("Admin password") as HTMLInputElement;
const storageInput = () => screen.getByLabelText(/WordPress storage/) as HTMLInputElement;

describe("password generation", () => {
  it("produces valid, distinct passwords", () => {
    const first = generatePassword();
    expect(first).toMatch(/^[\x21-\x7e]{16}$/);
    expect(generatePassword()).not.toBe(first);
  });

  it("starts with a generated password, regenerates it and copies it", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    renderModal();
    const initial = passwordInput().value;
    expect(initial).toHaveLength(16);

    fireEvent.click(screen.getByText("Generate"));
    expect(passwordInput().value).not.toBe(initial);

    await act(async () => fireEvent.click(screen.getByText("Copy")));
    expect(writeText).toHaveBeenCalledWith(passwordInput().value);
  });
});

describe("initial products", () => {
  it("starts with three example products and supports add and remove", () => {
    renderModal();
    expect(screen.getAllByLabelText("Product name")).toHaveLength(3);

    fireEvent.click(screen.getByText("Add product"));
    expect(screen.getAllByLabelText("Product name")).toHaveLength(4);

    fireEvent.click(screen.getByLabelText("Remove Denim Jeans"));
    const names = (screen.getAllByLabelText("Product name") as HTMLInputElement[]).map((i) => i.value);
    expect(names).toEqual(["Classic T-Shirt", "Sneakers", ""]);
  });

  it("rejects a product with a bad price", () => {
    renderModal();
    type(nameInput(), "demo");
    type(screen.getAllByLabelText("Price")[0], "abc");
    expect(launchButton().disabled).toBe(true);
    type(screen.getAllByLabelText("Price")[0], "499.50");
    expect(launchButton().disabled).toBe(false);
  });
});

describe("storage", () => {
  it("limits the selection to the remaining quota and explains the MySQL overhead", () => {
    renderModal();
    type(nameInput(), "demo");
    type(storageInput(), "2");
    expect(screen.getByText(/MySQL needs an additional 1 Gi. Total: 3 Gi/)).toBeTruthy();
    expect(launchButton().disabled).toBe(false);

    type(storageInput(), "4");
    expect(launchButton().disabled).toBe(true);
    expect(storageInput().max).toBe("3");
  });

  it("blocks launching when the store limit is reached", () => {
    usage.stores.used = 3;
    renderModal();
    type(nameInput(), "demo");
    expect(screen.getByText(/limit of 3 stores/)).toBeTruthy();
    expect(launchButton().disabled).toBe(true);
  });
});

describe("submitting", () => {
  it("sends the complete store specification", async () => {
    renderModal();
    type(nameInput(), "demo");
    type(passwordInput(), "Sup3rSecret!pw");
    type(storageInput(), "2");

    await act(async () => fireEvent.click(launchButton()));

    expect(posted).toEqual({
      name: "demo",
      admin_password: "Sup3rSecret!pw",
      storage_gi: 2,
      products: [
        { name: "Classic T-Shirt", price: "499", description: "A classic cotton t-shirt" },
        { name: "Denim Jeans", price: "1299", description: "Blue denim jeans" },
        { name: "Sneakers", price: "2499", description: "Comfortable running shoes" },
      ],
    });
    expect(onCreated).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("drops blank product rows", async () => {
    renderModal();
    type(nameInput(), "demo");
    fireEvent.click(screen.getByText("Add product"));
    await act(async () => fireEvent.click(launchButton()));
    expect((posted?.products as unknown[]).length).toBe(3);
  });

  it("shows the server error and stays open", async () => {
    response = new Response(JSON.stringify({ detail: "Storage quota exceeded" }), { status: 403 });
    renderModal();
    type(nameInput(), "demo");
    await act(async () => fireEvent.click(launchButton()));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Storage quota exceeded"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    renderModal();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("tells the user which payments are available", () => {
    renderModal();
    expect(screen.getByText(/Razorpay test payments \(UPI, cards\)/)).toBeTruthy();
    cleanup();
    usage.online_payments = false;
    renderModal();
    expect(screen.getByText(/Razorpay test payments are not configured/)).toBeTruthy();
  });
});
