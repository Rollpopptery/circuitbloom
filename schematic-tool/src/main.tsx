import { StrictMode, useState, useEffect, Component } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.tsx";
import { SimView } from "./sim/SimView.tsx";

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  render() {
    if (this.state.error) {
      return (
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
          height: "100vh", gap: 16, fontFamily: "sans-serif", color: "#ccc", background: "#1a1a1a",
        }}>
          <div style={{ fontSize: 18, color: "#ef5350" }}>Something went wrong</div>
          <pre style={{ fontSize: 12, color: "#888", maxWidth: 600, whiteSpace: "pre-wrap", textAlign: "center" }}>
            {(this.state.error as Error).message}
          </pre>
          <button onClick={() => window.location.reload()}
            style={{ padding: "8px 24px", background: "#1565C0", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer", fontSize: 14 }}>
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function AppRouter() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const handler = () => setHash(window.location.hash);
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);
  return hash === "#/sim" ? <SimView /> : <App />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <AppRouter />
    </ErrorBoundary>
  </StrictMode>,
);
