import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ConnectionDetails, BackupList, BackupResult } from "../lib/types";

type Props = { conn: ConnectionDetails; selectedDatabase: string | null };

export function BackupCenter({ conn, selectedDatabase }: Props) {
  const [list, setList] = useState<BackupList | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [last, setLast] = useState<BackupResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keep, setKeep] = useState<number>(5);
  const [rollbackPath, setRollbackPath] = useState<string>("");
  const [rollbackBusy, setRollbackBusy] = useState<boolean>(false);
  const [rollbackResult, setRollbackResult] = useState<string | null>(null);

  async function refresh() {
    setBusy(true);
    try {
      const r = await api.listBackups(selectedDatabase);
      setList(r);
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
      );
      setLast(res);
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
          {selectedDatabase ?? "no database selected"} · native BACKUP DATABASE · keep
          last <span className="font-mono">{keep}</span>
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
          {last.size_bytes.toLocaleString()} bytes ·{" "}
          {last.verified ? "verified" : "unverified"} ·{" "}
          kept {last.kept_after}
        </div>
      ) : null}
      {rollbackResult ? (
        <div className="hairline p-3 text-[11px] font-mono text-accent">{rollbackResult}</div>
      ) : null}
      {list && list.files.length > 0 ? (
        <div className="hairline">
          <p className="px-4 py-2 text-[11px] font-mono text-mute hairline-b">
            Rollback from backup
          </p>
          <div className="flex items-center gap-2 px-4 py-3">
            <select
              className="input flex-1 text-[11px]"
              value={rollbackPath}
              onChange={(e) => setRollbackPath(e.target.value)}
            >
              <option value="">-- select a backup to restore --</option>
              {list.files.map((f) => (
                <option key={f} value={f}>{f.split(/[\\/]/).pop()}</option>
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
      {list && list.files.length > 0 ? (
        <div className="hairline">
          <table className="w-full font-mono text-xs">
            <thead>
              <tr className="text-left text-mute">
                <th className="px-4 py-2 font-normal">file</th>
                <th className="px-2 py-2 font-normal text-right">size</th>
              </tr>
            </thead>
            <tbody>
              {list.files.map((p) => {
                const size = fileSize(p);
                return (
                  <tr key={p} className="hairline-t">
                    <td className="px-4 py-2 text-ink">{p}</td>
                    <td className="px-2 py-2 text-right text-mute">{size}</td>
                  </tr>
                );
              })}
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

function fileSize(path: string): string {
  // We don't have a stat API; show the path's last segment as a
  // visual cue. The .bak is the source of truth on disk.
  const seg = path.split(/[\\/]/).pop() ?? path;
  return seg;
}
