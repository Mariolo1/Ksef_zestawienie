export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, credentials: "same-origin", headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const r = await fetch(url, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = (data as { detail?: unknown }).detail;
    throw new ApiError(r.status, typeof d === "string" ? d : `Błąd ${r.status}`);
  }
  return data as T;
}

export const api = {
  get: <T>(u: string) => request<T>("GET", u),
  post: <T>(u: string, b?: unknown) => request<T>("POST", u, b),
  put: <T>(u: string, b?: unknown) => request<T>("PUT", u, b),
  del: <T>(u: string) => request<T>("DELETE", u),
};

export type User = { id: number; email: string; has_password: boolean; google: boolean };
export type Company = {
  nip: string; name: string; ksef_env: string; auth_method: "cert" | "token";
  has_token: boolean; has_credentials: boolean; has_certificate: boolean;
  cert_subject: string; cert_valid_to: string | null;
};
export type Report = {
  id: number; date_from: string; date_to: string; kind: string; date_type: string;
  status: "queued" | "running" | "done" | "error"; progress: string;
  invoices: number; errors: number; created_at: string; finished_at: string | null;
  downloadable: boolean;
};
