import { useRef, useState } from "react";

const API_URL = "http://localhost:8010";

const SUGGESTIONS = [
  "How many vehicles are idle?",
  "Propose a plan for today",
  "Predict delay for shipment 1",
  "What is the model health?",
  "Any anomalies?",
  "Add forbidden zone at lat 13.05 lng 80.25 radius 5",
];

// The copilot chats, explains and PROPOSES. Accept/reject buttons are the
// only way anything executes - and even they go through the guardrails.
function CopilotPanel() {
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const listEnd = useRef(null);

  const decide = async (proposalId, decision) => {
    setBusy(true);
    try {
      const res = await fetch(
        `${API_URL}/copilot/proposals/${proposalId}/decision`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, note: decision === "accepted"
            ? "accepted in console" : "rejected in console" }) });
      const body = await res.json();
      setMessages((prev) => [...prev, {
        role: "copilot",
        text: decision === "accepted"
          ? (body.outcome === "allowed"
            ? "Executed through the guardrails. Done."
            : "You accepted it, but the guardrails refused execution: " +
              JSON.stringify(body.result?.detail ?? {}).slice(0, 140))
          : "Rejected. Nothing was executed - recorded in the ledger.",
      }]);
    } finally {
      setBusy(false);
      listEnd.current?.scrollIntoView({ behavior: "smooth" });
    }
  };

  const send = async (q) => {
    const text = (q ?? question).trim();
    if (!text || busy) return;
    setBusy(true);
    setQuestion("");
    setMessages((prev) => [...prev, { role: "you", text }]);
    try {
      const res = await fetch(`${API_URL}/copilot/ask`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      const body = await res.json();
      setMessages((prev) => [...prev, {
        role: "copilot",
        text: body.answer,
        intent: body.intent,
        confidence: body.confidence,
        proposal: body.proposal,
        proposalId: body.proposal_id,
        requiresHuman: body.requires_human,
        guardrails: body.guardrail_results,
      }]);
    } catch (err) {
      setMessages((prev) => [...prev,
        { role: "copilot", text: "Could not reach the server: " + err.message }]);
    } finally {
      setBusy(false);
      listEnd.current?.scrollIntoView({ behavior: "smooth" });
    }
  };

  return (
    <div>
      <h2>AI copilot <small>(proposes — you decide)</small></h2>
      <div style={{ background: "#f9fafb", borderRadius: 8, padding: 12,
                    maxHeight: 340, overflowY: "auto" }}>
        {messages.length === 0 && (
          <p style={{ color: "#6b7280" }}>Ask me about the fleet, shipments,
            the model, or ask me to propose an action.</p>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ margin: "8px 0" }}>
            <b>{m.role === "you" ? "You" : "Copilot"}:</b> {m.text}
            {m.intent && (
              <div style={{ fontSize: 12, color: "#6b7280" }}>
                intent: {m.intent} · confidence {m.confidence}
              </div>
            )}
            {m.proposal && (
              <div style={{ border: "1px solid #f59e0b", borderRadius: 6,
                            padding: 8, marginTop: 6, background: "#fffbeb" }}>
                <div style={{ fontSize: 13 }}>
                  Proposal <code>{JSON.stringify(m.proposal)}</code>
                </div>
                {(m.guardrails?.length ?? 0) > 0 && (
                  <div style={{ fontSize: 12, color: "#6b7280", marginTop: 4 }}>
                    guardrails: {m.guardrails.map(
                      (g) => `${g.rule}:${g.severity}`).join(", ") || "none"}
                  </div>
                )}
                <button disabled={busy} style={{ marginRight: 6 }}
                        onClick={() => decide(m.proposalId, "accepted")}>
                  Accept
                </button>
                <button disabled={busy}
                        onClick={() => decide(m.proposalId, "rejected")}>
                  Reject
                </button>
              </div>
            )}
          </div>
        ))}
        <div ref={listEnd} />
      </div>
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="e.g. propose a plan for today"
          style={{ flex: 1, padding: 8 }}
        />
        <button disabled={busy} onClick={() => send()}>Ask</button>
      </div>
      <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap",
                    gap: 6 }}>
        {SUGGESTIONS.map((s) => (
          <button key={s} disabled={busy} onClick={() => send(s)}
                  style={{ fontSize: 12, padding: "4px 8px" }}>
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

export default CopilotPanel;
