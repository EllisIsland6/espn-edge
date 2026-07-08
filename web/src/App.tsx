import { useEffect, useState } from "react";
import { getHealth, type Health } from "./api";

// Phase 0 placeholder: confirms the web app is wired to the API. The real
// Portfolio Board (SPEC 8.3) lands in Phase 2.
export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "48px 24px" }}>
      <h1 style={{ letterSpacing: "-0.02em" }}>
        ESPN <span style={{ color: "var(--red)" }}>Edge</span>
      </h1>
      <p style={{ color: "var(--text-secondary)" }}>
        Multi-account, multi-league fantasy advantage tracker.
      </p>

      <div
        style={{
          marginTop: 24,
          padding: 16,
          background: "var(--bg-panel)",
          border: "1px solid var(--border)",
          borderRadius: 10,
        }}
      >
        <div style={{ color: "var(--text-muted)", fontSize: 12, textTransform: "uppercase" }}>
          Backend health
        </div>
        {error && <div style={{ color: "var(--red)" }}>API unreachable: {error}</div>}
        {!error && !health && <div style={{ color: "var(--text-secondary)" }}>checking…</div>}
        {health && (
          <table className="mono" style={{ marginTop: 8, borderSpacing: "12px 4px" }}>
            <tbody>
              <tr>
                <td style={{ color: "var(--text-muted)" }}>status</td>
                <td style={{ color: "var(--green-text)" }}>{health.status}</td>
              </tr>
              <tr>
                <td style={{ color: "var(--text-muted)" }}>season</td>
                <td>{health.season}</td>
              </tr>
              <tr>
                <td style={{ color: "var(--text-muted)" }}>db_path</td>
                <td>{health.db_path}</td>
              </tr>
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
