import { api } from "../api.js";

const statusEl = document.getElementById("api-status");
const detailEl = document.getElementById("api-detail");

try {
  const health = await api.get("/health");
  statusEl.textContent = "online";
  statusEl.className = "status-ok";
  detailEl.textContent = `v${health.version} · ${health.env}`;
} catch (err) {
  statusEl.textContent = "unreachable";
  statusEl.className = "status-bad";
  detailEl.textContent = err.message;
}
