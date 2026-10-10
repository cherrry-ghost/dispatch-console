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

const API_URL = "http://localhost:8010";
const NAMES = ["vehicles", "drivers", "locations", "shipments", "assignments"];
const STATUS_COLOR = { pending: "#888888", assigned: "#2563eb" };

function App() {
  const [data, setData] = useState({
    vehicles: [], drivers: [], locations: [], shipments: [], assignments: [],
  });
  const [error, setError] = useState(null);
  const [version, setVersion] = useState(0); // bump to reload everything

  useEffect(() => {
    Promise.all(
      NAMES.map((n) =>
        fetch(`${API_URL}/${n}`).then((res) => {
          if (!res.ok) throw new Error(`/${n} returned ${res.status}`);
          return res.json();
        })
      )
    )
      .then((results) =>
        setData(Object.fromEntries(NAMES.map((n, i) => [n, results[i]])))
      )
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

  return (
    <div style={{ width: "min(950px, 95vw)", textAlign: "left" }}>
      <h1>Dispatch Console</h1>
      {error && <p>Could not load data: {error}</p>}

      <h2>Map</h2>
      <MapContainer
        center={[11.8, 78.5]}
        zoom={7}
        style={{ height: "420px", width: "100%" }}
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
              pathOptions={{ color: STATUS_COLOR[s.status] || "#16a34a" }}
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
            radius={9}
            pathOptions={{ color: "#b91c1c", fillOpacity: 0.8 }}
          >
            <Tooltip permanent direction="top">
              {l.name} ({l.location_type})
            </Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>
      <p>Red dots = locations. Lines = shipments (grey pending, blue assigned).</p>

      <h2>Fleet</h2>
      {vehicles.length === 0 && <p>No vehicles yet.</p>}
      <table>
        <thead>
          <tr><th>Plate</th><th>Type</th><th>Capacity (kg)</th><th>Status</th></tr>
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

      <AssignmentForm
        shipments={shipments}
        vehicles={vehicles}
        drivers={drivers}
        onDone={() => setVersion((v) => v + 1)}
      />
      <ApprovalsPanel
        refreshKey={version}
        onDone={() => setVersion((v) => v + 1)}
      />
      <AuditViewer refreshKey={version} />
    </div>
  );
}

export default App;