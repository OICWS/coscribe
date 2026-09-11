import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/** Root-level safety net -- without this, an uncaught render error anywhere
 * in the tree (a malformed history entry from an old thread, an unexpected
 * field shape from a REST response, ...) unmounts the whole app and leaves
 * a blank white page with nothing but a console.error the average user
 * never opens (see the session-switching bug report: "一切换其他session看
 * 不到" / "settings里所有设置消失"). This turns that into a visible message
 * plus a reload button, so at minimum the failure is diagnosable instead of
 * indistinguishable from "nothing happened". */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
        <div className="text-sm font-medium">Something went wrong.</div>
        <div className="max-w-md break-words text-xs text-[var(--muted)]">{error.message}</div>
        <button
          type="button"
          className="rounded-md bg-[var(--accent)] px-3 py-1.5 text-sm text-[var(--accent-fg)]"
          onClick={() => window.location.reload()}
        >
          Reload
        </button>
      </div>
    );
  }
}
