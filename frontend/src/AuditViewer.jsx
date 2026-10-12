import { useEffect, useState } from "react";

import { API_URL } from "./api";
const OUTCOME_CLASS = {
  allowed: "badge badge-ok",
  blocked: "badge badge-danger",
  warning: "badge badge-warn",
};

export default function AuditViewer({ refreshKey }) {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);
  const [verify, setVerify] = useState(null);

  const load = () =>
    fetch(`${API_URL}/audit?limit=50`)
      .then((res) => {
        if (!res.ok) throw new Error(`/audit returned ${res.status}`);
        return res.json();
      })
      .then(setRows)
      .catch((err) => setError(err.message));

  useEffect(() => {
    load();
  }, [refreshKey]);

  const runVerify = () =>
    fetch(`${API_URL}/audit/verify`)
      .then((res) => res.json())
      .then(setVerify)
      .catch((err) => setError(err.message));

  return (
    <div>
      <h2>Audit Log</h2>
      <button onClick={load}>Refresh</button>{" "}
      <button onClick={runVerify}>Verify chain</button>
      {verify && (
        <p className={verify.ok ? "text-ok" : "text-danger"}>
          {verify.ok
            ? `Chain intact (${verify.checked} entries checked)`
            : `CHAIN BROKEN at entry #${verify.broken_at_id}: ${verify.reason}`}
        </p>
      )}
      {error && <p>Could not load audit log: {error}</p>}
      <table>
        <thead>
          <tr>
            <th>#</th><th>Time</th><th>Actor</th><th>Action</th>
            <th>Entity</th><th>Outcome</th><th>Details</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{new Date(r.created_at).toLocaleString()}</td>
              <td>{r.actor_type}{r.actor_id ? ` (${r.actor_id})` : ""}</td>
              <td>{r.action}</td>
              <td>{r.entity_type} {r.entity_id}</td>
              <td>
                <span className={OUTCOME_CLASS[r.outcome] || "badge badge-muted"}>
                  {r.outcome}
                </span>
              </td>
              <td>
                <details>
                  <summary>view</summary>
                  <pre style={{ maxWidth: "380px", overflow: "auto" }}>
                    {JSON.stringify(r.details, null, 2)}
                  </pre>
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}