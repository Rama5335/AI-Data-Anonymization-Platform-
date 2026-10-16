import { useState } from "react";
import { api } from "../lib/api";
import type {
  ConnectionDetails,
  UpdatePlan,
  UpdateResult,
} from "../lib/types";

type Props = { jobId: string | null; conn: ConnectionDetails; selectedDatabase: string | null };

export function ExecutionMonitor({ jobId, conn, selectedDatabase }: Props) {
  const [plans, setPlans] = useState<UpdatePlan[]>([]);
  const [results, setResults] = useState<UpdateResult[] | null>(null);
  const [busy, setBusy] = useState<"plan" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState<string>("");
  const [backupPath, setBackupPath] = useState<string>("");
  const [lastRun, setLastRun] = useState<{ dry_run: boolean; rows: number } | null>(null);

  if (!jobId || !selectedDatabase) {
    return (
      <div className="hairline p-6 text-center text-mute font-mono text-sm">
        Run a discovery + classification first.
      </div>
    );
  }

  async function handlePlan() {
    if (!jobId) return;
    setBusy("plan");
    setError(null);
    try {
      await api.prepareMasking(jobId);
      const r = await api.planUpdate(jobId, { ...conn, database: selectedDatabase });
      setPlans(r.plans);
      setResults(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function handleApply() {
    if (!jobId) return;
    setBusy("apply");
    setError(null);
    try {
      await api.prepareMasking(jobId);
      const r = await api.applyUpdate({
        job_id: jobId,
        connection: { ...conn, database: selectedDatabase },
        expected_backup_path: backupPath,
        confirm: confirmText === "MASK AND UPDATE",
        dry_run: false,
      });
      setResults(r.results);
      setLastRun({
        dry_run: r.dry_run,
        rows: r.results.reduce((s, x) => s + x.rows_updated, 0),
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Execution Monitor</p>
        <p className="text-xs text-mute">
          {selectedDatabase} · job <span className="font-mono">{jobId.slice(0, 8)}</span>
        </p>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-mono">
          <button type="button" className="btn-ghost" onClick={handlePlan} disabled={busy !== null}>
            {busy === "plan" ? "Planning…" : "Plan (dry run)"}
          </button>
        </div>
      </header>
      {error ? (
        <div className="hairline p-3 text-[11px] font-mono text-flag">{error}</div>
      ) : null}
      {lastRun ? (
        <div className="hairline p-3 text-[11px] font-mono">
          <span className={lastRun.dry_run ? "text-mute" : "text-accent"}>
            {lastRun.dry_run ? "DRY RUN" : "APPLIED"}
          </span>{" "}
          · {lastRun.rows} rows touched
        </div>
      ) : null}
      {plans.length > 0 ? (
        <div className="hairline">
          <p className="px-4 py-2 text-[10px] font-mono uppercase tracking-wider text-mute hairline-b">
            plan · {plans.length} column{plans.length === 1 ? "" : "s"}
          </p>
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal">table</th>
                <th className="px-2 py-2 font-normal">column</th>
                <th className="px-2 py-2 font-normal text-right">rows</th>
                <th className="px-2 py-2 font-normal text-right">batches</th>
              </tr>
            </thead>
            <tbody>
              {plans.map((p) => (
                <tr key={`${p.table}.${p.column}`} className="hairline-t">
                  <td className="px-4 py-2 text-ink">{p.table}</td>
                  <td className="px-2 py-2 text-ink">{p.column}</td>
                  <td className="px-2 py-2 text-right text-ink">{p.row_count_estimate}</td>
                  <td className="px-2 py-2 text-right text-mute">{p.batches}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {results ? (
        <div className="hairline">
          <p className="px-4 py-2 text-[10px] font-mono uppercase tracking-wider text-accent hairline-b">
            result · {results.length} column{results.length === 1 ? "" : "s"}
          </p>
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal">table</th>
                <th className="px-2 py-2 font-normal">column</th>
                <th className="px-2 py-2 font-normal text-right">rows</th>
                <th className="px-2 py-2 font-normal text-right">duration</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={`${r.table}.${r.column}`} className="hairline-t">
                  <td className="px-4 py-2 text-ink">{r.table}</td>
                  <td className="px-2 py-2 text-ink">{r.column}</td>
                  <td className="px-2 py-2 text-right text-ink">{r.rows_updated}</td>
                  <td className="px-2 py-2 text-right text-mute">{r.duration_ms}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div className="hairline p-4 space-y-3">
        <p className="text-[10px] font-mono uppercase tracking-wider text-mute">
          apply (live)
        </p>
        <label className="block text-[11px] font-mono">
          <span className="text-mute">expected_backup_path</span>
          <input
            className="input mt-1"
            placeholder="/var/opt/mssql/backups/anonymizer_app_*.bak"
            value={backupPath}
            onChange={(e) => setBackupPath(e.target.value)}
          />
        </label>
        <label className="block text-[11px] font-mono">
          <span className="text-mute">
            type <span className="text-ink">MASK AND UPDATE</span> to confirm
          </span>
          <input
            className="input mt-1"
            placeholder="MASK AND UPDATE"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="btn-accent"
          disabled={confirmText !== "MASK AND UPDATE" || !backupPath || busy !== null}
          onClick={handleApply}
        >
          {busy === "apply" ? "Applying…" : "Apply (irreversible)"}
        </button>
      </div>
    </div>
  );
}
