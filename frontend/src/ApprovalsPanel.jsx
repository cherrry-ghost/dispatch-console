import { useEffect, useState } from "react";

const API_URL = "http://localhost:8010";

export default function ApprovalsPanel({ refreshKey, onDone }) {
  const [rows, setRows] = useState([]);
  const [notes, setNotes] = useState({});
  const [message, setMessage] = useState(null);

  useEffect(() => {
    fetch(`${API_URL}/approvals`)
      .then((res) => {
        if (!res.ok) throw new Error(`/approvals returned ${res.status}`);
        return res.json();
      })
      .then((data) => setRows(Array.isArray(data) ? data : []))
      .catch((err) => setMessage(err.message));
  }, [refreshKey]);

  const decide = async (id, decision) => {
    const res = await fetch(`${API_URL}/approvals/${id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, note: notes[id] || "" }),
    });
    const body = await res.json();
    if (res.ok) {
      setMessage(`Approval #${id} ${decision}.`);
    } else if (body.detail && body.detail.message) {
      setMessage(
        `${body.detail.message}: ` +
          body.detail.violations.map((v) => v.message).join(" ")
      );
    } else {
      setMessage(JSON.stringify(body.detail));
    }
    onDone();
  };

  const pending = rows.filter((r) => r.status === "pending");
  const decided = rows.filter((r) => r.status !== "pending");

  return (
    <div>
      <h2>Approvals</h2>
      {message && <p><strong className="text-warn">{message}</strong></p>}
      {pending.length === 0 && <p>Nothing waiting for approval.</p>}
      {pending.map((a) => (
        <div key={a.id} className="item-box">
          <div>
            <strong>#{a.id}</strong> {a.action} for shipment {a.entity_id}
            {" "}(vehicle {a.request.vehicle_id}, driver {a.request.driver_id})
          </div>
          <ul>
            {a.warnings.map((w, i) => (
              <li key={i} className="text-warn">{w.message}</li>
            ))}
          </ul>
          <div className="form-row">
            <input
              placeholder="Note (optional)"
              value={notes[a.id] || ""}
              onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })}
            />
            <button className="primary" onClick={() => decide(a.id, "approved")}>
              Approve
            </button>
            <button className="danger" onClick={() => decide(a.id, "rejected")}>
              Reject
            </button>
          </div>
        </div>
      ))}
      {decided.length > 0 && (
        <details>
          <summary>Decided ({decided.length})</summary>
          <table>
            <thead>
              <tr><th>#</th><th>Shipment</th><th>Status</th><th>Note</th></tr>
            </thead>
            <tbody>
              {decided.map((a) => (
                <tr key={a.id}>
                  <td>{a.id}</td>
                  <td>{a.entity_id}</td>
                  <td>{a.status}</td>
                  <td>{a.decision_note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}