export type StoreStatus =
  | "requested"
  | "provisioning"
  | "initializing"
  | "ready"
  | "failed"
  | "deleting"
  | "deleted";

export interface Store {
  id: number;
  name: string;
  owner: string;
  namespace: string;
  status: StoreStatus;
  storage_gi: number;
  url: string | null;
  admin_url: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface StoreEvent {
  id: number;
  level: "info" | "error";
  message: string;
  created_at: string;
}

export interface AdminCredentials {
  username: string;
  password: string;
  admin_url: string | null;
}

export interface User {
  id: number;
  username: string;
  email: string;
}

export interface Quota {
  used: number;
  limit: number;
}

export interface Usage {
  stores: Quota;
  storage_gi: Quota;
  max_store_storage_gi: number;
  mysql_storage_gi: number;
  online_payments: boolean;
}

export interface ProductInput {
  name: string;
  price: string;
  description: string;
}

export interface StoreSpec {
  name: string;
  admin_password: string;
  storage_gi: number;
  products: ProductInput[];
}

export const ACTIVE_STATUSES: StoreStatus[] = [
  "requested",
  "provisioning",
  "initializing",
  "deleting",
];

export const isActive = (status: StoreStatus) => ACTIVE_STATUSES.includes(status);
