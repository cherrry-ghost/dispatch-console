import { useEffect, useState } from "react";
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Polyline,
  Tooltip,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";
import AuditViewer from "./AuditViewer";
import AssignmentForm from "./AssignmentForm";
import ApprovalsPanel from "./ApprovalsPanel";
import CopilotPanel from "./CopilotPanel";
import EventsFeed from "./EventsFeed";
import LoginBar from "./LoginBar";
import LiveVehicles from "./LiveVehicles";
import MediaPanel from "./MediaPanel";
import NotificationsPanel from "./NotificationsPanel";

const API_URL = "http://localhost:8010";
const NAMES = ["vehicles", "drivers", "locations", "shipments", "assignments"];
const STATUS_COLOR = { pending: "#64748b", assigned: "#38bdf8" };

const TABS = [
  ["overview", "Overview"],
  ["fleet", "Fleet"],
  ["operations", "Operations"],
  ["copilot", "AI Copilot"],
  ["media", "Media"],
  ["audit", "Audit"],
];

function App() {
  const [tab, setTab] = useState("overview");
  const [data, setData] = useState({
    vehicles: [], drivers: [], locations: [], shipments: [], assignments: [],
  });
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0); // bump to reload everything

  useEffect(() => {
    Promise.all(
      NAMES.map((n) =>
        fetch(`${API_URL}/${n}`).then((res) => {
          if (!res.ok) throw new Error(`/${n} returned ${res.status}`);
          return res.json();
        })
      ).concat([
        fetch(`${API_URL}/health`).then((res) =>
          res.ok ? res.json() : null
        ),
      ])
    )
      .then((results) => {
        const entries = NAMES.map((n, i) => [n, results[i]]);
        setData(Object.fromEntries(entries));
        setHealth(results[NAMES.length]);
      })
      .catch((err) => setError(err.message));
  }, [version]);

  const { vehicles, drivers, locations, shipments, assignments } = data;
  const locById = Object.fromEntries(locations.map((l) => [l.id, l]));
  const driverById = Object.fromEntries(drivers.map((d) => [d.id, d]));
  const shipById = Object.fromEntries(shipments.map((s) => [s.id, s]));
  const activeByVehicle = Object.fromEntries(
    assignments.filter((a) => a.ended_at === null).map((a) => [a.vehicle_id, a])
  );

  const fleetStatus = (vehicleId) => {
    const a = activeByVehicle[vehicleId];
    if (!a) return "Idle";
    const s = shipById[a.shipment_id];
    const dest = s && locById[s.destination_id];
    const driver = driverById[a.driver_id];
    return `Shipment #${a.shipment_id} to ${dest ? dest.name : "?"} (driver: ${driver ? driver.full_name : "?"})`;
  };

  const idle = vehicles.length - Object.keys(activeByVehicle).length;
  const pending = shipments.filter((s) => s.status === "pending").length;
  const activeAssignments = assignments.filter((a) => a.ended_at === null).length;
  const modelTrained = health?.delay_model === "trained";

  const kpis = [
    { lbl: "Vehicles", num: String(vehicles.length), sub: `${idle} idle` },
    { lbl: "Drivers", num: String(drivers.length), sub: "" },
    { lbl: "Pending shipments", num: String(pending), numClass: pending > 0 ? "warn" : "" },
    { lbl: "Active assignments", num: String(activeAssignments), sub: "" },
    { lbl: "Open roads", num: health ? String(health.open_roads) : "…", sub: "" },
    {
      lbl: "Delay model",
      num: health ? (modelTrained ? "Trained" : "Untrained") : "…",
      numClass: modelTrained ? "good" : "warn",
      sub: "",
    },
  ];

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-dot" />
            Dispatch Console
          </div>
          <nav className="tabs">
            {TABS.map(([id, label]) => (
              <button
                key={id}
                className={`tab${tab === id ? " active" : ""}`}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
          </nav>
          <LoginBar />
        </div>
      </header>

      <main className="page">
        {error && (
          <div className="card">
            <p className="text-danger">Could not load data: {error}</p>
          </div>
        )}

        {tab === "overview" && (
          <>
            <div className="kpis">
              {kpis.map((k) => (
                <div className="kpi" key={k.lbl}>
                  <div className={`num ${k.numClass || ""}`}>{k.num}</div>
                  <div className="lbl">{k.lbl}</div>
                  {k.sub && <div className="sub">{k.sub}</div>}
                </div>
              ))}
            </div>

            <div className="card">
              <h2>Network map</h2>
              <MapContainer
                center={[11.8, 78.5]}
                zoom={7}
                style={{ height: "480px", width: "100%" }}
              >
                <TileLayer
                  url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                  attribution="&copy; OpenStreetMap contributors"
                />
                {shipments.map((s) => {
                  const o = locById[s.origin_id];
                  const d = locById[s.destination_id];
                  if (!o || !d) return null;
                  return (
                    <Polyline
                      key={s.id}
                      positions={[[o.latitude, o.longitude], [d.latitude, d.longitude]]}
                      pathOptions={{ color: STATUS_COLOR[s.status] || "#34d399" }}
                    >
                      <Tooltip sticky>
                        Shipment #{s.id}: {s.description} ({s.status})
                      </Tooltip>
                    </Polyline>
                  );
                })}
                {locations.map((l) => (
                  <CircleMarker
                    key={l.id}
                    center={[l.latitude, l.longitude]}
                    radius={8}
                    pathOptions={{ color: "#f87171", fillColor: "#f87171", fillOpacity: 0.85, weight: 1 }}
                  >
                    <Tooltip permanent direction="top">
                      {l.name} ({l.location_type})
                    </Tooltip>
                  </CircleMarker>
                ))}
              </MapContainer>
              <p style={{ marginBottom: 0 }}>
                Red dots = locations. Lines = shipments (grey pending, blue assigned, green other).
              </p>
            </div>

            <div className="grid two-col">
              <div className="card" style={{ marginBottom: 0 }}>
                <LiveVehicles />
              </div>
              <div className="card" style={{ marginBottom: 0 }}>
                <EventsFeed />
              </div>
            </div>
          </>
        )}

        {tab === "fleet" && (
          <div className="card">
            <h2>Fleet</h2>
            {vehicles.length === 0 && <p>No vehicles yet.</p>}
            {vehicles.length > 0 && (
              <table>
                <thead>
                  <tr>
                    <th>Plate</th><th>Type</th><th>Capacity (kg)</th><th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {vehicles.map((v) => (
                    <tr key={v.id}>
                      <td>{v.plate}</td>
                      <td>{v.vehicle_type}</td>
                      <td>{v.capacity_kg}</td>
                      <td>{fleetStatus(v.id)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {tab === "operations" && (
          <>
            <div className="card">
              <AssignmentForm
                shipments={shipments}
                vehicles={vehicles}
                drivers={drivers}
                onDone={() => setVersion((v) => v + 1)}
              />
            </div>
            <div className="card">
              <ApprovalsPanel
                refreshKey={version}
                onDone={() => setVersion((v) => v + 1)}
              />
            </div>
            <div className="card">
              <NotificationsPanel />
            </div>
          </>
        )}

        {tab === "copilot" && (
          <div className="card">
            <CopilotPanel />
          </div>
        )}

        {tab === "media" && (
          <div className="card">
            <MediaPanel />
          </div>
        )}

        {tab === "audit" && (
          <div className="card">
            <AuditViewer refreshKey={version} />
          </div>
        )}
      </main>
    </>
  );
}

export default App;
