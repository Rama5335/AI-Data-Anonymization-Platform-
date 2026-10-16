import { useId } from "react";
import type { ConnectionDetails } from "../lib/types";

type Props = {
  value: ConnectionDetails;
  onChange: (next: ConnectionDetails) => void;
  onTest: () => void;
  onListDatabases: () => void;
  testing: boolean;
  listing: boolean;
};

export function ConnectionForm({
  value,
  onChange,
  onTest,
  onListDatabases,
  testing,
  listing,
}: Props) {
  const hostId = useId();
  const portId = useId();
  const userId = useId();
  const passId = useId();
  const dbId = useId();

  function patch<K extends keyof ConnectionDetails>(
    key: K,
    v: ConnectionDetails[K],
  ) {
    onChange({ ...value, [key]: v });
  }

  return (
    <section className="space-y-3">
      <header>
        <p className="font-display text-lg text-ink leading-none">Target</p>
        <p className="text-xs text-mute mt-1">
          Connect to a disposable MSSQL Server. Credentials are sent per
          request and never persisted.
        </p>
      </header>

      <div className="grid grid-cols-[1fr_5rem] gap-2">
        <div>
          <label htmlFor={hostId} className="block text-[10px] font-mono uppercase tracking-wider text-mute mb-1">
            host
          </label>
          <input
            id={hostId}
            className="input"
            value={value.host}
            onChange={(e) => patch("host", e.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor={portId} className="block text-[10px] font-mono uppercase tracking-wider text-mute mb-1">
            port
          </label>
          <input
            id={portId}
            className="input"
            type="number"
            min={1}
            max={65535}
            value={value.port}
            onChange={(e) => patch("port", Number(e.target.value))}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor={userId} className="block text-[10px] font-mono uppercase tracking-wider text-mute mb-1">
            username
          </label>
          <input
            id={userId}
            className="input"
            value={value.username ?? ""}
            onChange={(e) => patch("username", e.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div>
          <label htmlFor={passId} className="block text-[10px] font-mono uppercase tracking-wider text-mute mb-1">
            password
          </label>
          <input
            id={passId}
            className="input"
            type="password"
            value={value.password ?? ""}
            onChange={(e) => patch("password", e.target.value)}
            autoComplete="off"
          />
        </div>
      </div>

      <div>
        <label htmlFor={dbId} className="block text-[10px] font-mono uppercase tracking-wider text-mute mb-1">
          database (optional)
        </label>
        <input
          id={dbId}
          className="input"
          value={value.database ?? ""}
          onChange={(e) => patch("database", e.target.value || null)}
          spellCheck={false}
          autoComplete="off"
          placeholder="leave empty to list on the server"
        />
      </div>

      <div className="flex flex-wrap gap-2 pt-2">
        <button
          className="btn"
          onClick={onTest}
          disabled={testing || !value.host || !value.username}
        >
          {testing ? "Testing…" : "Test connection"}
        </button>
        <button
          className="btn-ghost"
          onClick={onListDatabases}
          disabled={listing || !value.host || !value.username}
        >
          {listing ? "Listing…" : "List databases"}
        </button>
      </div>
    </section>
  );
}
