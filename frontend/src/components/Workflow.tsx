import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ConnectionDetails, WorkflowState } from "../lib/types";

type Props = { jobId: string | null; conn: ConnectionDetails; selectedDatabase: string | null };

export function WorkflowView({ jobId, conn, selectedDatabase }: Props) {
  const [state, setState] = useState<WorkflowState | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [phases, setPhases] = useState<Record<string, string[]>>({});

  async function refresh() {
    if (!jobId) return;
    setBusy(true);
    try {
      const r = await api.getWorkflowState(jobId);
      setState(r);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  useEffect(() => {
    void api.getPhases().then(setPhases).catch(() => undefined);
  }, []);

  if (!jobId) {
    return (
      <div className="hairline p-6 text-center text-mute font-mono text-sm">
        Run a discovery first.
      </div>
    );
  }

  async function advance(to_phase: string) {
    if (!jobId) return;
    setBusy(true);
    setError(null);
    try {
      await api.transitionWorkflow(jobId, to_phase);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const next = state ? phases[state.phase] ?? [] : [];

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Workflow</p>
        {state ? (
          <p className="text-xs text-mute">
            phase <span className="font-mono text-ink">{state.phase}</span> · updated{" "}
            {new Date(state.updated_at).toLocaleString()}{" "}
            {state.is_stale ? <span className="text-flag">· STALE</span> : null}
          </p>
        ) : null}
        <div className="ml-auto flex items-center gap-2 text-[11px] font-mono">
          <button type="button" className="btn-ghost" onClick={() => void refresh()} disabled={busy}>
            {busy ? "…" : "Refresh"}
          </button>
        </div>
      </header>
      {error ? (
        <div className="hairline p-3 text-[11px] font-mono text-flag">{error}</div>
      ) : null}
      {state ? (
        <div className="hairline p-3 text-[11px] font-mono">
          target <span className="text-ink">{conn.host}:{conn.port}/{selectedDatabase ?? "—"}</span> · job{" "}
          <span className="text-ink">{state.job_id.slice(0, 8)}</span>
        </div>
      ) : null}
      {next.length > 0 ? (
        <div className="hairline p-3 flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-mono uppercase tracking-wider text-mute">
            advance to
          </span>
          {next.map((p) => (
            <button
              key={p}
              type="button"
              className="btn-ghost"
              onClick={() => void advance(p)}
              disabled={busy}
            >
              {p}
            </button>
          ))}
        </div>
      ) : state ? (
        <div className="hairline p-3 text-[11px] font-mono text-mute">
          Terminal phase; no further transitions.
        </div>
      ) : null}
    </div>
  );
}
