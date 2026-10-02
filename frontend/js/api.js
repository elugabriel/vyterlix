// Low-level API client. Pages don't use this directly: they import the ready-made,
// logged-in-aware client `api` from auth.js.

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

/**
 * @param baseUrl        e.g. "http://localhost:8000/api/v1"
 * @param getAccessToken returns the current access token (or null)
 * @param onExpired      called once when the API says the access token expired;
 *                       resolve true if a fresh token was obtained (request is retried)
 */
export function createApiClient({
  baseUrl,
  getAccessToken = () => null,
  onExpired = null,
  fetchImpl = (...args) => fetch(...args),
}) {
  const root = String(baseUrl ?? "").replace(/\/+$/, "");

  // body: JSON to send. form: a FormData (file upload; the browser sets the multipart
  // header). blob: true to get a file back (a download) instead of JSON.
  async function request(method, path, { body, form, blob = false, auth = true, retry = true } = {}) {
    const headers = { Accept: blob ? "*/*" : "application/json" };
    const token = auth ? getAccessToken() : null;
    if (token) headers.Authorization = `Bearer ${token}`;
    // "include" lets the browser send/receive the httpOnly refresh cookie; the API's
    // CORS allow-list decides which frontend origins may do this.
    const init = { method, headers, credentials: "include" };
    if (form !== undefined) {
      init.body = form;
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }

    let res;
    try {
      res = await fetchImpl(`${root}${path}`, init);
    } catch (err) {
      throw new ApiError(0, "network_error", "Could not reach Vyterlix. Check your connection and try again.");
    }

    if (res.ok) {
      if (blob) return res.blob();
      return res.status === 204 ? null : await res.json().catch(() => null);
    }

    const data = await res.json().catch(() => null);
    const e = data?.error ?? {};
    if (res.status === 401 && e.code === "token_expired" && retry && onExpired && (await onExpired())) {
      return request(method, path, { body, form, blob, auth, retry: false });
    }
    throw new ApiError(
      res.status,
      e.code ?? "http_error",
      e.message ?? `Request failed (${res.status})`,
      e.details ?? null,
      e.request_id ?? res.headers.get("x-request-id"),
    );
  }

  return {
    get: (path, opts) => request("GET", path, opts),
    post: (path, body, opts) => request("POST", path, { ...opts, body }),
    put: (path, body, opts) => request("PUT", path, { ...opts, body }),
    patch: (path, body, opts) => request("PATCH", path, { ...opts, body }),
    delete: (path, opts) => request("DELETE", path, opts),
    postForm: (path, form, opts) => request("POST", path, { ...opts, form }),
    getBlob: (path, opts) => request("GET", path, { ...opts, blob: true }),
  };
}
