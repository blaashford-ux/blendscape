import { Component, useEffect, useState, type ReactNode } from "react";
import { PlayerBar } from "./components/PlayerBar";
import { ProjectList } from "./components/ProjectList";
import { ProjectView } from "./components/ProjectView";
import { PlayerProvider } from "./state/usePlayer";

function useHashRoute(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <main className="page">
          <h1>Something broke</h1>
          <p className="error-text">{this.state.error.message}</p>
          <p>
            <a href="#/" onClick={() => this.setState({ error: null })}>
              Back to your mixes
            </a>
          </p>
        </main>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const hash = useHashRoute();
  const m = /^#\/p\/(p_[A-Za-z0-9]+)/.exec(hash);
  return (
    <PlayerProvider>
      <div className="app">
        <nav className="topbar">
          <a href="#/" className="brand" aria-label="Blendscape home">
            <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden="true">
              <path d="M4 20c5-12 9-12 12 0s7 12 12 0" fill="none" stroke="#7cc4ff" strokeWidth="3" strokeLinecap="round" />
              <path d="M4 14c5 12 9 12 12 0s7-12 12 0" fill="none" stroke="#ff9f6b" strokeWidth="3" strokeLinecap="round" opacity=".85" />
            </svg>
            Blendscape
          </a>
        </nav>
        <ErrorBoundary key={hash}>{m ? <ProjectView key={m[1]} id={m[1]} /> : <ProjectList />}</ErrorBoundary>
        <PlayerBar />
      </div>
    </PlayerProvider>
  );
}
