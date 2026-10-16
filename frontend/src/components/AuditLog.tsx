import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { AuditList } from "../lib/types";

type Props = { jobId: string | null };

export function AuditLogView({ jobId }: Props) {
  const [data, setData] = useState<AuditList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [scope, setScope] = useState<"job" | "all">("job");

  async function refresh() {
    setBusy(true);
    try {
      const r = jobId && scope === "job"
        ? await api.listAuditForJob(jobId)
        : await api.listAudit(200);
      setData(r);
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
  }, [jobId, scope]);

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Audit Log</p>
        <p className="text-xs text-mute">
          {data?.items.length ?? 0} events · append-only
        </p>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-mono">
          <button
            type="button"
            className={scope === "job" ? "btn" : "btn-ghost"}
            onClick={() => setScope("job")}
            disabled={!jobId}
          >
            this job
          </button>
          <button
            type="button"
            className={scope === "all" ? "btn" : "btn-ghost"}
            onClick={() => setScope("all")}
          >
            all
          </button>
          <button type="button" className="btn-ghost" onClick={() => void refresh()} disabled={busy}>
            {busy ? "…" : "Refresh"}
          </button>
        </div>
      </header>
      {error ? (
        <div className="hairline p-3 text-[11px] font-mono text-flag">{error}</div>
      ) : null}
      {data && data.items.length > 0 ? (
        <div className="hairline">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal w-44">occurred_at</th>
                <th className="px-2 py-2 font-normal">event_type</th>
                <th className="px-2 py-2 font-normal">outcome</th>
                <th className="px-2 py-2 font-normal">details</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((r) => (
                <tr key={r.id} className="hairline-t">
                  <td className="px-4 py-2 text-mute whitespace-nowrap">
                    {new Date(r.occurred_at).toLocaleString()}
                  </td>
                  <td className="px-2 py-2 text-ink">{r.event_type}</td>
                  <td className="px-2 py-2">
                    <span
                      className={
                        r.outcome === "success"
                          ? "pill-sensitivity-neutral"
                          : r.outcome === "failure"
                            ? "pill-sensitivity-sensitive"
                            : "pill"
                      }
                    >
                      {r.outcome}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-mute truncate max-w-[40rem]">
                    <code className="text-[10px]">{JSON.stringify(r.details)}</code>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="hairline p-6 text-center text-mute font-mono text-sm">
          No audit events yet.
        </div>
      )}
    </div>
  );
}
