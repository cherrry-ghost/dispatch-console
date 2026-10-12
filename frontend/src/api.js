// One place decides where the API lives.
// Priority: VITE_API_URL build variable, then the known cloud API when the
// console itself runs on Vercel, then localhost for development.
const CLOUD_API = "https://dispatch-console-74jf.onrender.com";
const API_URL =
  import.meta.env.VITE_API_URL ||
  (window.location.hostname.endsWith(".vercel.app")
    ? CLOUD_API
    : "http://localhost:8010");
const WS_BASE = API_URL.replace(/^http/, "ws");

export { API_URL, WS_BASE };
