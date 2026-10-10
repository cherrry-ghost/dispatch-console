import { useState } from "react";
import ApprovalsPanel from "./ApprovalsPanel";

const API_URL = "http://localhost:8010";

export default function AssignmentForm({ shipments, vehicles, drivers, onDone }) {
  const [shipmentId, setShipmentId] = useState("");
  const [vehicleId, setVehicleId] = useState("");
  const [driverId, setDriverId] = useState("");
  const [reason, setReason] = useState("");
  const [result, setResult] = useState(null); // {kind, text, violations}

  const submit = async () => {
    if (!shipmentId || !vehicleId || !driverId) {
      setResult({ kind: "error", text: "Pick a shipment, a vehicle and a driver." });
      return;
    }
    try {
                  const payload = {
        shipment_id: Number(shipmentId),
        vehicle_id: Number(vehicleId),
        driver_id: Number(driverId),
        reason: reason.trim() || "Assigned from dispatch console",
      };
      const res = await fetch(`${API_URL}/assignments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (res.ok) {
        setResult({
          kind: "ok",
          text: `Assigned (assignment #${body.id}).`,
          violations: body.warnings || [],
        });
      } else if (body.detail && body.detail.violations) {
        setResult({
          kind: "blocked",
          text: body.detail.message,
          violations: body.detail.violations,
        });
      } else {
        setResult({ kind: "error", text: JSON.stringify(body.detail) });
      }
      onDone(); // reload data and the audit log
    } catch (err) {
      setResult({ kind: "error", text: err.message });
    }
  };

  const color = { ok: "#16a34a", blocked: "#dc2626", error: "#ca8a04" };

  return (
    <div>
      <h2>Assign a Shipment</h2>
      <select value={shipmentId} onChange={(e) => setShipmentId(e.target.value)}>
        <option value="">Shipment...</option>
        {shipments.map((s) => (
          <option key={s.id} value={s.id}>
            #{s.id} {s.description} ({s.status})
          </option>
        ))}
      </select>{" "}
      <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value)}>
        <option value="">Vehicle...</option>
        {vehicles.map((v) => (
          <option key={v.id} value={v.id}>
            {v.plate} ({v.vehicle_type}, {v.capacity_kg} kg)
          </option>
        ))}
      </select>{" "}
      <select value={driverId} onChange={(e) => setDriverId(e.target.value)}>
        <option value="">Driver...</option>
        {drivers.map((d) => (
          <option key={d.id} value={d.id}>{d.full_name}</option>
        ))}
      </select>{" "}
      <input
        placeholder="Reason (optional)"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />{" "}
      <button onClick={submit}>Assign</button>

      {result && (
        <div style={{ color: color[result.kind], marginTop: "8px" }}>
          <strong>{result.text}</strong>
          {result.violations && result.violations.length > 0 && (
            <pre style={{ overflow: "auto" }}>
              {JSON.stringify(result.violations, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}