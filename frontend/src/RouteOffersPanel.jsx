import { useEffect, useState } from "react";
import { MapContainer, TileLayer, Polyline, CircleMarker, Tooltip } from "react-leaflet";

const API_URL = "http://localhost:8010";
const LABEL_STYLE = {
  safe: { color: "#34d399", weight: 4 },
  caution: { color: "#fbbf24", weight: 4 },
  needs_approval: { color: "#f87171", weight: 4, dashArray: "8 6" },
};

// Phase 14: dispatcher proposes 2-3 ranked routes for one pending shipment;
// the driver picks in the driver app; a risky pick waits for the manager.
export default function RouteOffersPanel({ shipments, vehicles, drivers, locations, onDone }) {
  const [shipmentId, setShipmentId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [driverId, setDriverId] = useState("");
  const [routes, setRoutes] = useState(null);
  const [picked, setPicked] = useState(new Set());
  const [offers, setOffers] = useState([]);
  const [message, setMessage] = useState(null);

  const pending = shipments.filter((s) => s.status === "pending");
  const locById = Object.fromEntries(locations.map((l) => [l.id, l]));
  const shipment = pending.find((s) => String(s.id) === shipmentId);

  const loadOffers = () =>
    fetch(`${API_URL}/route-offers`)
      .then((res) => (res.ok ? res.json() : []))
      .then(setOffers)
      .catch(() => {});
  useEffect(() => { loadOffers(); }, []);

  const chooseShipment = async (id) => {
    setShipmentId(id);
    setRoutes(null);
    setPicked(new Set());
    if (!id) return;
    const s = pending.find((x) => String(x.id) === id);
    if (!s) return;
    const res = await fetch(
      `${API_URL}/routes?from_id=${s.origin_id}&to_id=${s.destination_id}`);
    const body = await res.json();
    if (res.ok) setRoutes(body.routes);
    else setMessage(body.detail?.message || "No route options found");
  };

  const toggle = (i) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else if (next.size < 3) next.add(i);
      return next;
    });
  };

  const send = async () => {
    setMessage(null);
    if (!shipment || picked.size === 0 || !vehicleId || !driverId) {
      setMessage("Pick a shipment, at least one route, a vehicle and a driver.");
      return;
    }
    const res = await fetch(`${API_URL}/route-offers`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        shipment_id: shipment.id,
        vehicle_id: Number(vehicleId),
        driver_id: Number(driverId),
        option_indices: [...picked],
      }),
    });
    const body = await res.json();
    setMessage(res.ok
      ? `Offer #${body.offer_id} sent — the driver now picks a route in the driver app.`
      : (body.detail?.message || JSON.stringify(body.detail)));
    if (res.ok) { loadOffers(); onDone(); }
  };

  return (
    <div>
      <h2>Route offers <small>(propose routes — the driver picks)</small></h2>
      <div className="form-row">
        <select value={shipmentId} onChange={(e) => chooseShipment(e.target.value)}>
          <option value="">Pending shipment...</option>
          {pending.map((s) => (
            <option key={s.id} value={s.id}>#{s.id} {s.description}</option>
          ))}
        </select>
        <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
          <option value="">Vehicle...</option>
          {vehicles.map((v) => (
            <option key={v.id} value={v.id}>{v.plate} ({v.capacity_kg} kg)</option>
          ))}
        </select>
        <select value={driverId} onChange={(e) => setDriverId(e.target.value)}>
          <option value="">Driver...</option>
          {drivers.slice(0, 60).map((d) => (
            <option key={d.id} value={d.id}>{d.full_name}</option>
          ))}
        </select>
        <button className="primary" onClick={send}>Send route offer</button>
      </div>

      {routes && shipment && (
        <>
          <MapContainer
            center={[
              locById[shipment.origin_id]?.latitude ?? 11.8,
              locById[shipment.origin_id]?.longitude ?? 78.5,
            ]}
            zoom={8}
            style={{ height: "300px", width: "100%", margin: "12px 0" }}
          >
            <TileLayer
              url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution="&copy; OpenStreetMap contributors"
            />
            {routes.map((r, ri) => {
              const pts = r.path
                .map((id) => locById[id])
                .filter(Boolean)
                .map((l) => [l.latitude, l.longitude]);
              return (
                <Polyline key={ri} positions={pts}
                          pathOptions={LABEL_STYLE[r.label] || LABEL_STYLE.caution}>
                  <Tooltip sticky>
                    Route {ri + 1}: {r.label} · {r.distance_km} km · max risk {r.max_risk}
                  </Tooltip>
                </Polyline>
              );
            })}
            {[shipment.origin_id, shipment.destination_id].map((id) => {
              const l = locById[id];
              if (!l) return null;
              return (
                <CircleMarker key={id} center={[l.latitude, l.longitude]}
                              radius={8}
                              pathOptions={{ color: "#f87171", fillOpacity: 0.85 }}>
                  <Tooltip permanent direction="top">{l.name}</Tooltip>
                </CircleMarker>
              );
            })}
          </MapContainer>
          <div className="form-row">
            {routes.map((r, ri) => (
              <label key={ri} className="chip" style={{ cursor: "pointer" }}>
                <input type="checkbox" style={{ marginRight: 6 }}
                       checked={picked.has(ri)} onChange={() => toggle(ri)} />
                Route {ri + 1}: <span className={
                  r.label === "safe" ? "text-ok"
                  : r.label === "caution" ? "text-warn" : "text-danger"}>
                  {r.label}</span> · {r.distance_km} km
              </label>
            ))}
          </div>
        </>
      )}
      {message && <p className="text-warn" style={{ marginBottom: 0 }}><strong>{message}</strong></p>}

      {offers.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary>Offers sent ({offers.length})</summary>
          <table>
            <thead>
              <tr><th>#</th><th>Shipment</th><th>Driver</th><th>Vehicle</th>
                  <th>Options</th><th>Status</th></tr>
            </thead>
            <tbody>
              {offers.map((o) => (
                <tr key={o.id}>
                  <td>{o.id}</td>
                  <td>#{o.shipment_id} {o.description}</td>
                  <td>{o.driver_name}</td>
                  <td>{o.plate}</td>
                  <td className="muted" style={{ fontSize: 12 }}>
                    {(o.options || []).map((op) => op.label).join(", ")}
                  </td>
                  <td>
                    <span className={`badge ${o.status === "claimed" ? "badge-ok" : "badge-muted"}`}>
                      {o.status}{o.chosen_index != null ? ` (route ${o.chosen_index + 1})` : ""}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}
