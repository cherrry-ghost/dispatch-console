import { useEffect, useState } from "react";

import { API_URL } from "./api";

const VERDICT_CLASS = {
  clean: "badge v-clean",
  suspicious: "badge v-suspicious",
  likely_fake: "badge v-likely_fake",
  unverified: "badge v-unverified",
};

// Phase 11: what the media checker thinks about uploaded photos/videos.
// The verdict is advice for the human, never the decision itself.
function MediaPanel() {
  const [checks, setChecks] = useState([]);
  const [error, setError] = useState(null);

  const load = () => {
    fetch(`${API_URL}/media/checks?limit=15`)
      .then((res) => res.json())
      .then(setChecks)
      .catch((err) => setError(err.message));
  };
  useEffect(() => { load(); }, []);

  return (
    <div>
      <h2>
        Media authenticity{" "}
        <small>(automatic checks — a human still decides)</small>
        <button onClick={load} className="chip" style={{ marginLeft: 8 }}>
          Refresh
        </button>
      </h2>
      {error && <p>Could not load checks: {error}</p>}
      {checks.length === 0 && !error && (
        <p>No photos or videos analysed yet.</p>
      )}
      {checks.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Verdict</th><th>Trust</th><th>What</th>
              <th>File</th><th>Signals</th><th>When</th>
            </tr>
          </thead>
          <tbody>
            {checks.map((c) => {
              const cls = VERDICT_CLASS[c.verdict] || "badge v-unverified";
              const flags = (c.reasons || [])
                .filter((r) => r.severity !== "info")
                .map((r) => r.flag);
              return (
                <tr key={c.id}>
                  <td>
                    <span className={cls}>
                      {c.verdict}
                    </span>
                  </td>
                  <td>{c.score}/100</td>
                  <td>
                    {c.media_kind === "evidence" ? "Incident evidence" : "Delivery proof"}{" "}
                    #{c.ref_id}
                  </td>
                  <td style={{ fontFamily: "monospace", fontSize: 12 }}>
                    {c.filename}
                  </td>
                  <td style={{ fontSize: 12, color: "#8ba0bf" }}>
                    {flags.length ? flags.join(", ") : "none"}
                  </td>
                  <td style={{ fontSize: 12, color: "#8ba0bf" }}>
                    {String(c.checked_at).slice(0, 16).replace("T", " ")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default MediaPanel;
