import { useEffect, useRef, useState } from "react";

const API_URL = "http://localhost:8010";
const WS_URL = "ws://localhost:8010/ws/live";

// Live vehicle tracking: subscribes to the server's /ws/live channel and
// shows every vehicle position as it arrives, with zone alerts highlighted.
function LiveVehicles({ onZoneAlert }) {
  const [positions, setPositions] = useState({});
  const [connected, setConnected] = useState(false);
  const alertsRef = useRef(onZoneAlert);
  alertsRef.current = onZoneAlert;

  useEffect(() => {
    // Seed with whatever the server already knows, then go live.
    fetch(`${API_URL}/vehicles/positions`)
      .then((res) => (res.ok ? res.json() : []))
      .then((rows) =>
        setPositions(
          Object.fromEntries(
            rows.map((p) => [
              p.vehicle_id,
              { lat: p.latitude, lng: p.longitude, speed: p.speed_kmh,
                source: p.source, alert: null },
            ])
          )
        )
      )
      .catch(() => {});

    const ws = new WebSocket(WS_URL);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type !== "position") return;
        setPositions((prev) => ({
          ...prev,
          [msg.vehicle_id]: {
            lat: msg.latitude, lng: msg.longitude, speed: msg.speed_kmh,
            source: msg.source, alert: msg.zone_alert,
          },
        }));
        if (msg.zone_alert && alertsRef.current) {
          alertsRef.current(
            `Vehicle ${msg.vehicle_id} entered forbidden zone "${msg.zone_alert.zone_name}"`
          );
        }
      } catch {
        // ignore malformed frames
      }
    };
    return () => ws.close();
  }, []);

  const entries = Object.entries(positions);

  return (
    <div>
      <h2>
        Live tracking{" "}
        <small className={`live-dot ${connected ? "on" : "off"}`}>
          {connected ? "● LIVE" : "● OFFLINE"}
        </small>
      </h2>
      {entries.length === 0 && <p>No vehicle positions yet.</p>}
      {entries.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Vehicle</th><th>Latitude</th><th>Longitude</th>
              <th>Speed (km/h)</th><th>Source</th><th>Zone</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([id, p]) => (
              <tr key={id} className={p.alert ? "row-alert" : ""}>
                <td>#{id}</td>
                <td>{p.lat.toFixed(4)}</td>
                <td>{p.lng.toFixed(4)}</td>
                <td>{p.speed ?? "–"}</td>
                <td>{p.source}</td>
                <td>
                  {p.alert
                    ? <span className="badge badge-danger">⚠ {p.alert.zone_name}</span>
                    : <span className="badge badge-muted">ok</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default LiveVehicles;
