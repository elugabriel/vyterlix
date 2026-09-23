// Runtime config. Loaded as a classic <script> BEFORE any module on every page.
// There is no build step, so each environment's deploy overwrites this one file.
window.VYTERLIX_CONFIG = Object.freeze({
  apiBaseUrl: "http://localhost:8000/api/v1",
});
