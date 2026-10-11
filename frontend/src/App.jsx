import { useEffect, useState } from "react";
import {
  MapContainer,
  TileLayer,
  CircleMarker,
  Circle,
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
const NAMES = ["vehicles", "drivers", "locations", "shipments", "assignments", "zones"];
const STATUS_COLOR = { pending: "#64748b", assigned: "#38bdf8" };
const ZONE_STYLE = {
  forbidden: { color: "#f87171", fillColor: "#f87171", fillOpacity: 0.14, weight: 1.5 },
  alert: { color: "#fbbf24", fillColor: "#fbbf24", fillOpacity: 0.10, weight: 1.5 },
};

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
    zones: [],
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

  const { vehicles, drivers, locations, shipments, assignments, zones } = data;
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

  const licenceBadge = (expiry) => {
    if (!expiry) return <span className="badge badge-muted">no licence</span>;
    const days = Math.floor((new Date(expiry) - Date.now()) / 86400000);
    if (days < 0) return <span className="badge badge-danger">expired</span>;
    if (days < 60) return <span className="badge badge-warn">{days} days left</span>;
    return <span className="badge badge-ok">{String(expiry).slice(0, 10)}</span>;
  };

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
                {zones.map((z) => (
                  <Circle
                    key={`zone-${z.id}`}
                    center={[z.center_lat, z.center_lng]}
                    radius={z.radius_km * 1000}
                    pathOptions={z.active
                      ? (ZONE_STYLE[z.zone_type] || ZONE_STYLE.alert)
                      : { color: "#5f7495", fillColor: "#5f7495",
                          fillOpacity: 0.05, weight: 1, dashArray: "4 6" }}
                  >
                    <Tooltip sticky>
                      {z.zone_type === "forbidden" ? "FORBIDDEN" : "ALERT"} zone: {z.name} ({z.radius_km} km)
                      {z.active ? "" : " — inactive"}
                    </Tooltip>
                  </Circle>
                ))}
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
                Shaded areas = zones: <span className="text-danger">red forbidden</span> (routing avoids, entry alerts),
                <span className="text-warn"> amber alert</span> (warning only).
              </p>
            </div>

            <div className="card">
              <h2>Zones — what the shaded areas mean</h2>
              <p style={{ marginTop: 0 }}>
                A zone is an area we draw on the map (name, centre point, radius in km).
                <b> Forbidden zones</b> are danger areas (war, conflict, disaster): before any
                route is calculated, every road inside them is removed from the map, so
                planned deliveries <b>automatically go around them</b>. If a truck drives in
                anyway, live tracking raises a loud warning — it never remotely blocks the truck.
                <b> Alert zones</b> are caution areas: routing may pass through, but crossing
                one fires an event. <b>Inactive</b> zones are kept for the record only and
                affect nothing.
              </p>
              {zones.length === 0 && <p>No zones drawn yet.</p>}
              {zones.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>Name</th><th>Type</th><th>Radius</th><th>Status</th><th>What it does</th>
                    </tr>
                  </thead>
                  <tbody>
                    {zones.map((z) => (
                      <tr key={`zrow-${z.id}`}>
                        <td>{z.name}</td>
                        <td>
                          <span className={`badge ${
                            !z.active ? "badge-muted"
                            : z.zone_type === "forbidden" ? "badge-danger" : "badge-warn"
                          }`}>
                            {z.zone_type === "forbidden" ? "Forbidden" : "Alert"}
                          </span>
                        </td>
                        <td>{z.radius_km} km</td>
                        <td>{z.active
                          ? <span className="text-ok">Active</span>
                          : <span className="muted">Inactive</span>}</td>
                        <td>
                          {!z.active
                            ? "Not in effect — kept for the record only."
                            : z.zone_type === "forbidden"
                              ? "Routing goes around it; entering it triggers a live warning."
                              : "Routing may pass through; crossing it fires an event."}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
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
          <>
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
            <div className="card">
              <h2>Drivers — licence status</h2>
              <p style={{ marginTop: 0, marginBottom: 10 }}>
                Guardrails refuse any assignment to a driver whose licence has expired.
              </p>
              {drivers.length === 0 && <p>No drivers yet.</p>}
              {drivers.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>#</th><th>Driver</th><th>Licence No.</th>
                      <th>Licence expiry</th><th>Hazmat</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drivers.slice(0, 40).map((d) => (
                      <tr key={d.id}>
                        <td>{d.id}</td>
                        <td>{d.full_name}</td>
                        <td>{d.licence_number}</td>
                        <td>{licenceBadge(d.licence_expiry)}</td>
                        <td>{d.hazmat_certified
                          ? <span className="badge badge-muted">hazmat</span>
                          : <span className="muted">–</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {drivers.length > 40 && (
                <p className="muted" style={{ marginBottom: 0 }}>
                  Showing first 40 of {drivers.length} drivers.
                </p>
              )}
            </div>
          </>
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
