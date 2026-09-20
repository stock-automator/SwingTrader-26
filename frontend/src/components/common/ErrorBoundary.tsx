import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Shown in the fallback message for context, e.g. a tab name. */
  label?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Catches a render/lifecycle error anywhere in its subtree so one broken
 * tab can't take down the whole app - the same "isolate the failure"
 * intent as the per-tab `Suspense` boundaries already used for
 * code-splitting (`App.tsx`), extended to cover uncaught render errors
 * instead of just the loading state. Must be a class component - React has
 * no hook equivalent to `getDerivedStateFromError`/`componentDidCatch`.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(
      `ErrorBoundary${this.props.label ? ` (${this.props.label})` : ""}:`,
      error,
      info.componentStack,
    );
  }

  private reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div
          role="alert"
          data-testid="error-boundary-fallback"
          className="rounded border border-short-dim bg-short-dim/10 p-6 text-center text-sm text-text-dim"
        >
          <div className="font-semibold text-short">
            {this.props.label
              ? `${this.props.label} hit an error.`
              : "Something went wrong."}
          </div>
          {/* Most tabs unmount on tab-switch anyway (clearing this state
              for free), but the always-mounted Live Screener tab
              (App.tsx - never unmounts, it owns the header's live regime
              WebSocket) doesn't, so a manual reset is the only way back
              short of a full page reload. */}
          <button
            type="button"
            onClick={this.reset}
            className="mt-2 rounded border border-border bg-panel-alt px-3 py-1.5 text-xs font-medium text-text hover:border-accent"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
