import { useEffect, useRef, useState } from "react";

const WS_URL = "ws://localhost:8010/ws/events";
const API_URL = "http://localhost:8010";

const SEVERITY_COLOR = {
  info: "#8ba0bf",
  warning: "#fbbf24",
};

// Live event feed: geofence crossings, road closures, assignment milestones.
// Seeded from history, then updated in real time via /ws/events.
function EventsFeed() {
  const [events, setEvents] = useState([]);
  const [connected, setConnected] = useState(false);
  const listRef = useRef(null);

  useEffect(() => {
    fetch(`${API_URL}/events?limit=25`)
      .then((res) => (res.ok ? res.json() : []))
      .then((rows) => setEvents(rows))
      .catch(() => {});

    const ws = new WebSocket(WS_URL);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        setEvents((prev) => [msg, ...prev].slice(0, 25));
      } catch {
        // ignore malformed frames
      }
    };
    return () => ws.close();
  }, []);

  const describe = (e) => {
    const who = e.vehicle_id ? `vehicle #${e.vehicle_id}` : "the network";
    switch (e.kind) {
      case "geofence.enter":
        return `${who} entered geofence "${e.details?.zone}"`;
      case "geofence.exit":
        return `${who} left geofence "${e.details?.zone}"`;
      case "vehicle.in_zone":
        return `${who} is on FORBIDDEN ground ("${e.details?.zone}")`;
      case "vehicle.left_zone":
        return `${who} left the forbidden zone "${e.details?.zone}"`;
      case "road.closed":
        return `Road #${e.details?.road_edge_id} closed`;
      case "road.reopened":
        return `Road #${e.details?.road_edge_id} reopened`;
      case "assignment.created":
        return `Shipment #${e.shipment_id} assigned to vehicle #${e.vehicle_id}`;
      case "notification.sent":
        return `Customer told (shipment #${e.shipment_id}): ${e.details?.kind} — "${e.details?.message}"`;
      default:
        return e.kind;
    }
  };

  return (
    <div>
      <h2>
        Events{" "}
        <small className={`live-dot ${connected ? "on" : "off"}`}>
          {connected ? "● LIVE" : "● OFFLINE"}
        </small>
      </h2>
      {events.length === 0 && <p>No events yet.</p>}
      {events.length > 0 && (
        <ul ref={listRef} className="feed">
          {events.map((e) => (
            <li key={e.id ?? `${e.kind}-${e.created_at}`}>
              <span style={{ color: SEVERITY_COLOR[e.severity] || "#8ba0bf" }}>
                {describe(e)}
              </span>{" "}
              <small>({new Date(e.created_at).toLocaleTimeString()})</small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default EventsFeed;
