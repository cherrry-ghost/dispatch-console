import { useEffect, useState } from "react";

const API_URL = "http://localhost:8010";

// Customer notifications history: what we told whom, and when.
function NotificationsPanel() {
  const [notes, setNotes] = useState([]);

  useEffect(() => {
    const load = () =>
      fetch(`${API_URL}/notifications?limit=15`)
        .then((res) => (res.ok ? res.json() : []))
        .then(setNotes)
        .catch(() => {});
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div>
      <h2>Customer notifications</h2>
      {notes.length === 0 && <p>No customer messages yet.</p>}
      {notes.length > 0 && (
        <ul style={{ maxHeight: "220px", overflowY: "auto" }}>
          {notes.map((n) => (
            <li key={n.id}>
              <b>{n.recipient}</b> — shipment #{n.shipment_id} ({n.kind}):{" "}
              {n.message}{" "}
              <small>({new Date(n.sent_at).toLocaleTimeString()})</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default NotificationsPanel;
