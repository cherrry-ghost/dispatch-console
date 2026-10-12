import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// Attach the stored login token to every API call, whatever component
// made it. GETs stay open server-side; this matters for POSTs.
import { API_URL } from "./api";
const originalFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  if (url.startsWith(API_URL)) {
    const token = localStorage.getItem("token");
    if (token) {
      const headers = new Headers(init.headers || {});
      if (!headers.has("Authorization")) {
        headers.set("Authorization", `Bearer ${token}`);
      }
      init = { ...init, headers };
    }
  }
  return originalFetch(input, init);
};

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
