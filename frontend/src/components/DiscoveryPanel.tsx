import { useId } from "react";

type Props = {
  databases: string[];
  selectedDatabase: string | null;
  onSelectDatabase: (db: string | null) => void;
  schemas: string[];
  selectedSchema: string | null;
  onSelectSchema: (s: string | null) => void;
  onDiscover: () => void;
  onListSchemas: () => void;
  onListObjects?: () => void;
  discovering: boolean;
  listingSchemas: boolean;
  tablesAndViews: { name: string; object_type: string }[];
};

export function DiscoveryPanel(props: Props) {
  const dbId = useId();
  const schemaId = useId();

  return (
    <section className="space-y-3">
      <header>
        <p className="font-display text-lg text-ink leading-none">Discovery</p>
        <p className="text-xs text-mute mt-1">
          Browse databases and schemas, then run a read-only schema
          discovery.
        </p>
      </header>

      <div>
        <label
          htmlFor={dbId}
          className="block text-[10px] font-mono uppercase tracking-wider text-mute mb-1"
        >
          database
        </label>
        <select
          id={dbId}
          className="input"
          value={props.selectedDatabase ?? ""}
          onChange={(e) => props.onSelectDatabase(e.target.value || null)}
        >
          <option value="">— pick one —</option>
          {props.databases.map((db) => (
            <option key={db} value={db}>
              {db}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label
          htmlFor={schemaId}
          className="block text-[10px] font-mono uppercase tracking-wider text-mute mb-1"
        >
          schema
        </label>
        <select
          id={schemaId}
          className="input"
          value={props.selectedSchema ?? ""}
          onChange={(e) => props.onSelectSchema(e.target.value || null)}
          disabled={!props.selectedDatabase}
        >
          <option value="">— all user schemas —</option>
          {props.schemas.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-wrap gap-2 pt-1">
        <button
          className="btn-ghost"
          onClick={props.onListSchemas}
          disabled={props.listingSchemas || !props.selectedDatabase}
        >
          {props.listingSchemas ? "Loading schemas…" : "List schemas"}
        </button>
        <button
          className="btn"
          onClick={props.onDiscover}
          disabled={props.discovering || !props.selectedDatabase}
        >
          {props.discovering ? "Discovering…" : "Run schema discovery"}
        </button>
      </div>

      {props.tablesAndViews.length > 0 ? (
        <div className="hairline p-3 space-y-1">
          <p className="text-[10px] font-mono uppercase tracking-wider text-mute">
            discovered · {props.tablesAndViews.length} objects
          </p>
          <ul className="font-mono text-xs space-y-0.5 max-h-48 overflow-auto">
            {props.tablesAndViews.map((o) => (
              <li key={`${o.object_type}.${o.name}`} className="flex justify-between gap-2">
                <span className="text-ink truncate">{o.name}</span>
                <span className="text-mute shrink-0">{o.object_type}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
