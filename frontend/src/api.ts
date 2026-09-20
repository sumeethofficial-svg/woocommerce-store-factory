import type { AdminCredentials, Store, StoreEvent, StoreSpec, Usage, User } from "./types";

const API_URL = import.meta.env.VITE_API_URL ?? "";
const TOKEN_KEY = "store-factory.token";

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
  } catch {
    return response.statusText;
  }
  return response.statusText;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = tokenStore.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!response.ok) throw new ApiError(response.status, await errorMessage(response));
  return response.json() as Promise<T>;
}

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  register: (username: string, email: string, password: string) =>
    request<User>("/api/auth/register", jsonBody("POST", { username, email, password })),
  login: (username: string, password: string) =>
    request<{ access_token: string }>("/api/auth/login", {
      method: "POST",
      body: new URLSearchParams({ username, password }),
    }),
  me: () => request<User>("/api/auth/me"),
  listStores: () => request<Store[]>("/api/stores"),
  usage: () => request<Usage>("/api/stores/usage"),
  createStore: (spec: StoreSpec) => request<Store>("/api/stores", jsonBody("POST", spec)),
  deleteStore: (id: number) => request<Store>(`/api/stores/${id}`, { method: "DELETE" }),
  retryStore: (id: number) => request<Store>(`/api/stores/${id}/retry`, { method: "POST" }),
  events: (id: number) => request<StoreEvent[]>(`/api/stores/${id}/events`),
  credentials: (id: number) => request<AdminCredentials>(`/api/stores/${id}/credentials`),
};

export const messageOf = (error: unknown) =>
  error instanceof Error ? error.message : "Something went wrong";
