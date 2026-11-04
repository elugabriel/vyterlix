// Production runtime config. The deploy copies this over frontend/js/config.js on the server.
// The website and the API share one address (nginx sends /api/ to the app), so the path is enough.
window.VYTERLIX_CONFIG = Object.freeze({
  apiBaseUrl: "/api/v1",
});
