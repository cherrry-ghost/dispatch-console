// One place decides where the API lives.
// Local dev: nothing to set (defaults to localhost:8010).
// Cloud: set VITE_API_URL at build time (e.g. the Render URL).
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8010";
const WS_BASE = API_URL.replace(/^http/, "ws");

export { API_URL, WS_BASE };
