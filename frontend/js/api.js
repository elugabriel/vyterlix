// Shared API client. Every page talks to the backend through this — never raw fetch().

export class ApiError extends Error {
  constructor(status, code, message, details = null, requestId = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

export function createApiClient({ baseUrl, fetchImpl = (...args) => fetch(...args) }) {
  const root = String(baseUrl ?? "").replace(/\/+$/, "");

  async function request(method, path, { body, headers = {} } = {}) {
    const init = { method, headers: { Accept: "application/json", ...headers } };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }

    let res;
    try {
      res = await fetchImpl(`${root}${path}`, init);
    } catch (err) {
      throw new ApiError(0, "network_error", "Could not reach the Vyterlix API", String(err));
    }

    const data = res.status === 204 ? null : await res.json().catch(() => null);
    if (!res.ok) {
      const e = data?.error ?? {};
      throw new ApiError(
        res.status,
        e.code ?? "http_error",
        e.message ?? `Request failed (${res.status})`,
        e.details ?? null,
        e.request_id ?? res.headers.get("x-request-id"),
      );
    }
    return data;
  }

  return {
    get: (path, opts) => request("GET", path, opts),
    post: (path, body, opts) => request("POST", path, { ...opts, body }),
    put: (path, body, opts) => request("PUT", path, { ...opts, body }),
    patch: (path, body, opts) => request("PATCH", path, { ...opts, body }),
    delete: (path, opts) => request("DELETE", path, opts),
  };
}

export const api = createApiClient({ baseUrl: globalThis.VYTERLIX_CONFIG?.apiBaseUrl });
