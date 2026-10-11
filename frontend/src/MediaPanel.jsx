import { useEffect, useState } from "react";

const API_URL = "http://localhost:8010";

const VERDICT_STYLE = {
  clean: { color: "#166534", bg: "#dcfce7" },
  suspicious: { color: "#92400e", bg: "#fef3c7" },
  likely_fake: { color: "#991b1b", bg: "#fee2e2" },
  unverified: { color: "#374151", bg: "#e5e7eb" },
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
  useEffect(load, []);

  return (
    <div>
      <h2>
        Media authenticity{" "}
        <small>(automatic checks — a human still decides)</small>
        <button onClick={load} style={{ marginLeft: 8, fontSize: 12 }}>
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
              const style = VERDICT_STYLE[c.verdict] || {};
              const flags = (c.reasons || [])
                .filter((r) => r.severity !== "info")
                .map((r) => r.flag);
              return (
                <tr key={c.id}>
                  <td>
                    <span
                      style={{
                        background: style.bg, color: style.color,
                        padding: "2px 10px", borderRadius: 999,
                        fontWeight: 600, fontSize: 13,
                      }}
                    >
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
                  <td style={{ fontSize: 12, color: "#6b7280" }}>
                    {flags.length ? flags.join(", ") : "none"}
                  </td>
                  <td style={{ fontSize: 12, color: "#6b7280" }}>
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
