import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Server, CreateServerRequest, ProbeServerResult } from "../lib/types";

type Draft = {
  id?: string;
  name: string;
  host: string;
  port: number;
  auth_type: "sql" | "windows";
};

const EMPTY_DRAFT: Draft = { name: "", host: "", port: 1433, auth_type: "sql" };

export function ServersTab({
  onActivate,
  activeServerId,
}: {
  onActivate?: (s: Server) => void;
  activeServerId?: string | null;
}) {
  const [servers, setServers] = useState<Server[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [probeResult, setProbeResult] = useState<ProbeServerResult | null>(null);
  const [probeFor, setProbeFor] = useState<string | null>(null);
  const [probeUser, setProbeUser] = useState<string>("sa");
  const [probePass, setProbePass] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    setBusy(true);
    try {
      const list = await api.listServers();
      setServers(list);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function save() {
    if (!draft) return;
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const payload: CreateServerRequest = {
        name: draft.name,
        host: draft.host,
        port: draft.port,
        auth_type: draft.auth_type,
      };
      if (draft.id) {
        await api.updateServer(draft.id, payload);
        setMsg(`Updated ${draft.name}`);
      } else {
        const s = await api.createServer(payload);
        setMsg(`Created ${s.name} (id=${s.id.slice(0, 8)})`);
      }
      setDraft(null);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function del(s: Server) {
    if (!window.confirm(`Delete server ${s.name}?`)) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteServer(s.id);
      setMsg(`Deleted ${s.name}`);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function probe() {
    if (!probeFor) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.probeServer(probeFor, {
        username: probeUser,
        password: probePass,
      });
      setProbeResult(r);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Servers</p>
        <p className="text-xs text-mute">
          {servers.length} registered · add, edit, delete; use "probe" to test connectivity
        </p>
        <div className="ml-auto flex items-center gap-2 text-[11px] font-mono">
          <button
            type="button"
            className="btn-accent"
            onClick={() => setDraft(EMPTY_DRAFT)}
            disabled={busy}
          >
            New server
          </button>
          <button type="button" className="btn-ghost" onClick={() => void refresh()} disabled={busy}>
            Refresh
          </button>
        </div>
      </header>

      {error ? (
        <div className="hairline p-3 text-[11px] font-mono text-flag">{error}</div>
      ) : null}
      {msg ? (
        <div className="hairline p-3 text-[11px] font-mono text-accent">{msg}</div>
      ) : null}

      <div className="hairline">
        <table className="w-full font-mono text-xs">
          <thead>
            <tr className="text-left text-mute">
              <th className="px-4 py-2 font-normal">name</th>
              <th className="px-2 py-2 font-normal">host</th>
              <th className="px-2 py-2 font-normal">port</th>
              <th className="px-2 py-2 font-normal">auth</th>
              <th className="px-2 py-2 font-normal">id</th>
              <th className="px-2 py-2 font-normal text-right">actions</th>
            </tr>
          </thead>
          <tbody>
            {servers.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-3 text-mute text-center">
                  No servers yet. Click "New server".
                </td>
              </tr>
            ) : null}
            {servers.map((s) => (
              <tr key={s.id} className="hairline-t">
                <td className="px-4 py-2 text-ink">{s.name}</td>
                <td className="px-2 py-2 text-ink">{s.host}</td>
                <td className="px-2 py-2 text-ink">{s.port}</td>
                <td className="px-2 py-2 text-ink">{s.auth_type}</td>
                <td className="px-2 py-2 text-mute">{s.id.slice(0, 8)}</td>
                <td className="px-2 py-2 text-right space-x-1 whitespace-nowrap">
                  {onActivate ? (
                    <button
                      type="button"
                      className={s.id === activeServerId ? "pill-sensitivity-sensitive" : "btn-ghost"}
                      onClick={() => onActivate(s)}
                    >
                      {s.id === activeServerId ? "Active" : "Use"}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => {
                      setDraft({
                        id: s.id,
                        name: s.name,
                        host: s.host,
                        port: s.port,
                        auth_type: s.auth_type,
                      });
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => {
                      setProbeFor(s.id);
                      setProbeResult(null);
                    }}
                  >
                    Probe
                  </button>
                  <button type="button" className="btn-ghost" onClick={() => void del(s)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {draft ? (
        <div className="hairline p-4 space-y-3">
          <p className="text-[10px] font-mono uppercase tracking-wider text-mute">
            {draft.id ? "Edit server" : "New server"}
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <label className="text-[11px] font-mono">
              <span className="text-mute">name</span>
              <input
                className="input mt-1"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                required
              />
            </label>
            <label className="text-[11px] font-mono">
              <span className="text-mute">host</span>
              <input
                className="input mt-1"
                value={draft.host}
                onChange={(e) => setDraft({ ...draft, host: e.target.value })}
                required
              />
            </label>
            <label className="text-[11px] font-mono">
              <span className="text-mute">port</span>
              <input
                className="input mt-1"
                type="number"
                min={1}
                max={65535}
                value={draft.port}
                onChange={(e) => setDraft({ ...draft, port: Number(e.target.value) })}
                required
              />
            </label>
            <label className="text-[11px] font-mono">
              <span className="text-mute">auth</span>
              <select
                className="input mt-1"
                value={draft.auth_type}
                onChange={(e) =>
                  setDraft({ ...draft, auth_type: e.target.value as "sql" | "windows" })
                }
              >
                <option value="sql">sql</option>
                <option value="windows">windows</option>
              </select>
            </label>
          </div>
          <div className="flex items-center gap-2 text-[11px] font-mono">
            <button type="button" className="btn-accent" onClick={() => void save()} disabled={busy}>
              {busy ? "Saving…" : draft.id ? "Save changes" : "Create"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {probeFor ? (
        <div className="hairline p-4 space-y-3">
          <p className="text-[10px] font-mono uppercase tracking-wider text-mute">
            probe · server {probeFor.slice(0, 8)}…
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <label className="text-[11px] font-mono">
              <span className="text-mute">username</span>
              <input
                className="input mt-1"
                value={probeUser}
                onChange={(e) => setProbeUser(e.target.value)}
              />
            </label>
            <label className="text-[11px] font-mono">
              <span className="text-mute">password (not stored)</span>
              <input
                className="input mt-1"
                type="password"
                value={probePass}
                onChange={(e) => setProbePass(e.target.value)}
              />
            </label>
          </div>
          <div className="flex items-center gap-2 text-[11px] font-mono">
            <button
              type="button"
              className="btn-accent"
              onClick={() => void probe()}
              disabled={busy || !probePass}
            >
              {busy ? "Probing…" : "Run probe"}
            </button>
            <button
              type="button"
              className="btn-ghost"
              onClick={() => {
                setProbeFor(null);
                setProbeResult(null);
              }}
            >
              Close
            </button>
          </div>
          {probeResult ? (
            <div className="hairline p-3 text-[11px] font-mono">
              {probeResult.reachable ? (
                <span className="text-accent">reachable</span>
              ) : (
                <span className="text-flag">unreachable: {probeResult.error}</span>
              )}
              {probeResult.databases ? (
                <div className="mt-1 text-mute">
                  databases: {probeResult.databases.join(", ")}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
