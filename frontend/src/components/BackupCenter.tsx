import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import type {
  BackupList,
  BackupRecord,
  BackupResult,
  ConnectionDetails,
} from "../lib/types";

type Props = {
  conn: ConnectionDetails;
  selectedDatabase: string | null;
  jobId: string | null;
};

export function BackupCenter({ conn, selectedDatabase, jobId }: Props) {
  const [list, setList] = useState<BackupList | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [last, setLast] = useState<BackupResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keep, setKeep] = useState<number>(5);
  const [rollbackPath, setRollbackPath] = useState<string>("");
  const [rollbackBusy, setRollbackBusy] = useState<boolean>(false);
  const [rollbackResult, setRollbackResult] = useState<string | null>(null);

  const records = useMemo<BackupRecord[]>(() => {
    if (!list) return [];
    if (list.records.length > 0) return list.records;
    // Backwards compatibility with an API that only returned paths.
    return list.files.map((path) => ({
      backup_path: path,
      database: selectedDatabase ?? "",
      size_bytes: 0,
      verified: false,
      created_at: "",
      job_id: null,
    }));
  }, [list, selectedDatabase]);

  const verifiedRecords = useMemo(
    () => records.filter((r) => r.verified),
    [records],
  );

  async function refresh() {
    setBusy(true);
    try {
      const r = await api.listBackups(selectedDatabase);
      setList(r);
      setError(null);
      const newestVerified = r.records.find((rec) => rec.verified);
      if (newestVerified) {
        setRollbackPath((current) => current || newestVerified.backup_path);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDatabase]);

  async function runBackup() {
    if (!selectedDatabase) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.runBackup(
        { ...conn, database: selectedDatabase },
        selectedDatabase,
        keep,
        jobId,
      );
      setLast(res);
      setRollbackPath(res.backup_path);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function runRollback() {
    if (!selectedDatabase || !rollbackPath) return;
    if (!window.confirm(`RESTORE DATABASE [${selectedDatabase}] from backup? This will overwrite current data.`)) return;
    setRollbackBusy(true);
    setRollbackResult(null);
    setError(null);
    try {
      const res = await api.rollback({
        connection: { ...conn, database: selectedDatabase },
        backup_path: rollbackPath,
        database: selectedDatabase,
      });
      setRollbackResult(res.message);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRollbackBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Backup Center</p>
        <p className="text-xs text-mute">
          {selectedDatabase ?? "no database selected"}
          {jobId ? (
            <>
              {" "}· job <span className="font-mono">{jobId.slice(0, 8)}</span>
            </>
          ) : null}{" "}
          · native BACKUP DATABASE · keep last{" "}
          <span className="font-mono">{keep}</span>
        </p>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-mono">
          <label className="text-mute">keep</label>
          <input
            type="number"
            min={1}
            max={1000}
            className="input w-16"
            value={keep}
            onChange={(e) => setKeep(Number(e.target.value))}
          />
          <button
            type="button"
            className="btn-accent"
            onClick={runBackup}
            disabled={!selectedDatabase || busy}
          >
            {busy ? "Backing up…" : "Run backup"}
          </button>
          <button type="button" className="btn-ghost" onClick={() => void refresh()} disabled={busy}>
            {busy ? "…" : "Refresh"}
          </button>
        </div>
      </header>
      {error ? (
        <div className="hairline p-3 text-[11px] font-mono text-flag">{error}</div>
      ) : null}
      {last ? (
        <div className="hairline p-3 text-[11px] font-mono">
          <span className="text-accent">✓</span> last backup ·{" "}
          <span className="text-ink">{last.backup_path}</span> ·{" "}
          {last.size_bytes > 0 ? formatBytes(last.size_bytes) : "size unavailable"} ·{" "}
          {last.verified ? "verified" : "unverified"} ·{" "}
          kept {last.kept_after}
        </div>
      ) : null}
      {rollbackResult ? (
        <div className="hairline p-3 text-[11px] font-mono text-accent">{rollbackResult}</div>
      ) : null}
      {verifiedRecords.length > 0 ? (
        <div className="hairline">
          <p className="px-4 py-2 text-[11px] font-mono text-mute hairline-b">
            Rollback from verified backup
          </p>
          <div className="flex items-center gap-2 px-4 py-3">
            <select
              className="input flex-1 text-[11px]"
              value={rollbackPath}
              onChange={(e) => setRollbackPath(e.target.value)}
            >
              <option value="">-- select a verified backup to restore --</option>
              {verifiedRecords.map((rec) => (
                <option key={rec.backup_path} value={rec.backup_path}>
                  {fileName(rec.backup_path)} · {formatBytes(rec.size_bytes)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn-ghost text-flag border-flag"
              onClick={() => void runRollback()}
              disabled={!rollbackPath || rollbackBusy || !selectedDatabase}
            >
              {rollbackBusy ? "Restoring..." : "Restore"}
            </button>
          </div>
        </div>
      ) : null}
      {records.length > 0 ? (
        <div className="hairline">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal">file</th>
                <th className="px-2 py-2 font-normal">database</th>
                <th className="px-2 py-2 font-normal text-right">size</th>
                <th className="px-2 py-2 font-normal">state</th>
                <th className="px-2 py-2 font-normal">created</th>
                <th className="px-4 py-2 font-normal">job</th>
              </tr>
            </thead>
            <tbody>
              {records.map((rec) => (
                <tr key={rec.backup_path} className="hairline-t">
                  <td className="px-4 py-2 text-ink">{fileName(rec.backup_path)}</td>
                  <td className="px-2 py-2 text-ink">{rec.database || "—"}</td>
                  <td className="px-2 py-2 text-right text-mute">
                    {rec.size_bytes > 0 ? formatBytes(rec.size_bytes) : "—"}
                  </td>
                  <td className="px-2 py-2">
                    {rec.verified ? (
                      <span className="text-accent">verified</span>
                    ) : (
                      <span className="text-flag">unverified</span>
                    )}
                  </td>
                  <td className="px-2 py-2 text-mute">{formatTime(rec.created_at)}</td>
                  <td className="px-4 py-2 text-mute">
                    {rec.job_id ? rec.job_id.slice(0, 8) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="hairline p-6 text-center text-mute font-mono text-sm">
          No backups yet for {selectedDatabase ?? "this database"}.
        </div>
      )}
      <p className="text-[10px] font-mono text-mute">
        Backup dir: <span className="text-ink">{list?.backup_dir ?? "—"}</span>{" "}
        (configurable via BACKUP_DIR / BACKUP_KEEP env vars).
      </p>
    </div>
  );
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatTime(value: string): string {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
