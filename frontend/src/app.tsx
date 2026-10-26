import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { LoginPage } from "./auth/LoginPage";
import { ConnectionForm } from "./components/ConnectionForm";
import { DiscoveryPanel } from "./components/DiscoveryPanel";
import { LiveWire } from "./components/LiveWire";
import { SchemaViewer } from "./components/SchemaViewer";
import { ApprovalCenter } from "./components/ApprovalCenter";
import { BackupCenter } from "./components/BackupCenter";
import { ExecutionMonitor } from "./components/ExecutionMonitor";
import { AuditLogView } from "./components/AuditLog";
import { WorkflowView } from "./components/Workflow";
import { SettingsView } from "./components/Settings";
import { ServersTab } from "./components/ServersTab";
import { api } from "./lib/api";
import type {
  ConnectionDetails,
  CreateUserRequest,
  DiscoveredTableOut,
  ObjectInfo,
} from "./lib/types";

type Log = { at: number; label: string; ok: boolean };
type Tab =
  | "dashboard"
  | "schema"
  | "approval"
  | "backup"
  | "execution"
  | "audit"
  | "workflow"
  | "settings"
  | "servers"
  | "users";

const DEFAULT_CONNECTION: ConnectionDetails = {
  host: "127.0.0.1",
  port: 1433,
  auth_type: "sql",
  username: "sa",
  password: "",
  database: "anonymizer_app",
  driver: "ODBC Driver 18 for SQL Server",
  encrypt: true,
  trust_server_certificate: true,
  timeout_seconds: 10,
};

const TAB_TITLES: Record<Tab, string> = {
  dashboard: "Dashboard",
  schema: "Schema & Sample",
  approval: "Approval Center",
  backup: "Backup Center",
  execution: "Execution Monitor",
  audit: "Audit Log",
  workflow: "Workflow",
  settings: "Settings",
  servers: "Servers",
  users: "Users (admin)",
};
function Inner() {
  const { state, logout } = useAuth();

  // Show the login page until we know we have a session.
  if (state.kind === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center text-mute font-mono">
        …
      </div>
    );
  }
  if (state.kind === "anonymous") {
    return <LoginPage />;
  }

  return <AppShell user={state.user} logout={logout} />;
}

function AppShell(props: { user: { id: string; username: string; role: string }; logout: () => void }) {
  const isAdmin = props.user.role === "admin";
  const [conn, setConn] = useState<ConnectionDetails>(DEFAULT_CONNECTION);
  // When the user picks a registered server, we pre-fill the discovery
  // form's host/port with that server's values. The actual credentials
  // are still typed by the user in the ConnectionForm.
  const [activeServer, setActiveServer] = useState<{ id: string; name: string; host: string; port: number } | null>(null);
  const [databases, setDatabases] = useState<string[]>([]);
  const [schemas, setSchemas] = useState<string[]>([]);
  const [tables, setTables] = useState<{ name: string; object_type: string }[]>([]);
  const [selectedDatabase, setSelectedDatabase] = useState<string | null>(null);
  const [selectedSchema, setSelectedSchema] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [discovered, setDiscovered] = useState<DiscoveredTableOut[]>([]);
  const [status, setStatus] = useState<"idle" | "live" | "stale" | "error">("idle");
  const [lastQuery, setLastQuery] = useState<{ at: number; label: string }>();
  const [log, setLog] = useState<Log[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [, startTransition] = useTransition();
  const [tab, setTab] = useState<Tab>("dashboard");
  const [authError, setAuthError] = useState<string | null>(null);

  const [testing, setTesting] = useState(false);
  const [listing, setListing] = useState(false);
  const [listingSchemas, setListingSchemas] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [pendingApprovals, setPendingApprovals] = useState<number | null>(null);
  const [approvalCount, setApprovalCount] = useState<number | null>(null);
  const [workflowPhase, setWorkflowPhase] = useState<string | null>(null);

// Workspace sidebar open / close
   const [sidebarOpen, setSidebarOpen] = useState(true);

  const target = useMemo(() => {
    const db = selectedDatabase ?? "—";
    return `${conn.username ?? "?"}@${conn.host}:${conn.port}/${db}`;
  }, [conn, selectedDatabase]);

  const logIt = useCallback((label: string, ok: boolean) => {
    setLog((l) => [{ at: Date.now(), label, ok }, ...l].slice(0, 8));
  }, []);

  const succeed = useCallback(
    (label: string) => {
      setStatus("live");
      setLastQuery({ at: Date.now(), label });
      logIt(label, true);
      setError(null);
      setAuthError(null);
    },
    [logIt],
  );

  const fail = useCallback(
    (label: string, msg: string) => {
      setStatus("error");
      logIt(label, false);
      setError(msg);
      if (msg.toLowerCase().includes("401")) {
        setAuthError("Session expired — please sign in again.");
        props.logout();
      }
    },
    [logIt, props],
  );

  async function handleTest() {
    setTesting(true);
    try {
      await api.testConnection(conn);
      succeed("test-connection");
    } catch (e) {
      fail("test-connection", (e as Error).message);
    } finally {
      setTesting(false);
    }
  }

  async function handleListDatabases() {
    setListing(true);
    try {
      const r = await api.listDatabases(conn);
      succeed("list-databases");
      setDatabases(r.databases);
      if (r.databases.length > 0 && !selectedDatabase) {
        setSelectedDatabase(r.databases[0]);
      }
    } catch (e) {
      fail("list-databases", (e as Error).message);
    } finally {
      setListing(false);
    }
  }

  async function handleListSchemas() {
    if (!selectedDatabase) return;
    setListingSchemas(true);
    try {
      const r = await api.listSchemas({ ...conn, database: selectedDatabase });
      succeed("list-schemas");
      setSchemas(r.schemas);
    } catch (e) {
      fail("list-schemas", (e as Error).message);
    } finally {
      setListingSchemas(false);
    }
  }

  async function handleListObjects() {
    if (!selectedDatabase) return;
    try {
      const r = await api.listObjects(
        { ...conn, database: selectedDatabase },
        "TABLE",
      );
      const v = await api.listObjects(
        { ...conn, database: selectedDatabase },
        "VIEW",
      );
      const merged: ObjectInfo[] = [...r.objects, ...v.objects];
      succeed("list-objects");
      startTransition(() => {
        setTables(merged.map((o) => ({ name: o.name, object_type: o.object_type })));
      });
    } catch (e) {
      fail("list-objects", (e as Error).message);
    }
  }

  async function handleDiscover() {
    if (!selectedDatabase) return;
    setDiscovering(true);
    try {
      const target: ConnectionDetails = { ...conn, database: selectedDatabase };
      const r = await api.discoverSchema(target);
      succeed(r.cached ? "schema-discover (cached)" : "schema-discover");
      setJobId(r.job_id);
      const job = await api.getJobSchema(r.job_id);
      startTransition(() => {
        setDiscovered(job.tables);
        setTables(
          job.tables.map((t) => ({
            name: t.table_name,
            object_type: t.object_type,
          })),
        );
      });
    } catch (e) {
      fail("schema-discover", (e as Error).message);
    } finally {
      setDiscovering(false);
    }
  }

  // Liveness ping for the LiveWire: every 30s.
  useEffect(() => {
    const t = window.setInterval(() => {
      api.health().then(() => setStatus("live")).catch(() => setStatus("error"));
    }, 30_000);
    return () => window.clearInterval(t);
  }, []);

  // Live dashboard counters for the current job. Refetched when the
  // Dashboard tab becomes visible so decisions made elsewhere (approval,
  // workflow transitions) are reflected immediately.
  useEffect(() => {
    if (!jobId) {
      setPendingApprovals(null);
      setApprovalCount(null);
      setWorkflowPhase(null);
      return;
    }
    let cancelled = false;
    void Promise.allSettled([
      api.listPending(jobId),
      api.listApprovals(jobId),
      api.getWorkflowState(jobId),
    ]).then(([pending, approvals, workflow]) => {
      if (cancelled) return;
      setPendingApprovals(
        pending.status === "fulfilled" ? pending.value.items.length : null,
      );
      setApprovalCount(
        approvals.status === "fulfilled"
          ? approvals.value.filter(
              (a) =>
                !a.excluded &&
                (a.decision === "approve" || a.decision === "override"),
            ).length
          : null,
      );
      setWorkflowPhase(
        workflow.status === "fulfilled" ? workflow.value.phase : null,
      );
    });
    return () => {
      cancelled = true;
    };
  }, [jobId, tab]);

  return (
    <div className="min-h-screen flex flex-col">
      <LiveWire
        status={status}
        lastQueryLabel={lastQuery?.label}
        lastQueryAt={lastQuery?.at}
        target={target}
      />

      <header className="hairline-b">
        <div className="mx-auto max-w-7xl px-6 py-5 flex items-baseline gap-4">
          <h1 className="font-display text-2xl text-ink leading-none">
            AI Data Anonymization Platform
          </h1>
          <p className="text-xs text-mute font-mono">
            discovery · schema · sampling · classification · approval · backup · update · audit
          </p>
          <div className="ml-auto flex items-center gap-3 text-[11px] font-mono">
            <span className="text-mute">user</span>
            <span className="text-ink">{props.user.username}</span>
            <span className="pill-sensitivity-neutral">{props.user.role}</span>
            <button type="button" className="btn-ghost" onClick={props.logout}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      {authError ? (
        <div className="bg-card hairline mx-auto max-w-7xl mt-2 p-3 text-xs font-mono text-flag">
          {authError}
        </div>
      ) : null}

      <main className="flex-1">
        <div className="mx-auto max-w-7xl px-6 py-8 space-y-6">
          
<nav className="flex flex-col gap-1 rounded-2xl border border-pink-100 bg-white p-3 shadow-sm">
  <p className="px-3 pb-2 pt-1 text-xs font-semibold uppercase tracking-widest text-purple-400">
    Workspace
  </p>

  
<div className="mb-4">
  <button
    type="button"
    onClick={() => setSidebarOpen((open) => !open)}
    aria-expanded={sidebarOpen}
    className="flex w-full items-center justify-between rounded-2xl border border-pink-200 bg-white px-5 py-4 text-left font-semibold text-pink-800 shadow-sm transition hover:bg-pink-50"
  >
    <span className="flex items-center gap-3">
      <span className="text-xl">{sidebarOpen ? "✕" : "☰"}</span>
      <span>Workspace</span>
    </span>

    <span className="text-pink-500">
      {sidebarOpen ? "Close" : "Open"}
    </span>
  </button>

  {sidebarOpen && (
    <div className="mt-3 grid grid-cols-1 gap-2 rounded-2xl border border-pink-100 bg-white p-3 shadow-sm sm:grid-cols-2 lg:grid-cols-1">
      {(Object.keys(TAB_TITLES) as Tab[])
        .filter((t) => (t === "users" ? isAdmin : true))
        .map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => {
              setTab(t);
              setSidebarOpen(false);
            }}
            className={
              t === tab
                ? "w-full rounded-xl border border-pink-200 bg-pink-100 px-4 py-3 text-left text-sm font-semibold text-pink-800 shadow-sm transition-colors"
                : "w-full rounded-xl px-4 py-3 text-left text-sm text-slate-600 transition-colors hover:bg-pink-50 hover:text-pink-700"
            }
          >
            {TAB_TITLES[t]}
          </button>
        ))}
    </div>
  )}
</div>

</nav>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <aside className="lg:col-span-1 space-y-6">
              <div className="bg-card hairline p-5 space-y-6">
                {activeServer ? (
                  <div className="pill-sensitivity-sensitive flex items-center justify-between">
                    <span className="text-ink">
                      {activeServer.name} · {activeServer.host}:{activeServer.port}
                    </span>
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => setActiveServer(null)}
                    >
                      Clear
                    </button>
                  </div>
                ) : null}
                <ConnectionForm
                  value={conn}
                  onChange={setConn}
                  onTest={handleTest}
                  onListDatabases={handleListDatabases}
                  testing={testing}
                  listing={listing}
                />
                <hr className="hairline" />
                <DiscoveryPanel
                  databases={databases}
                  selectedDatabase={selectedDatabase}
                  onSelectDatabase={(db) => {
                    setSelectedDatabase(db);
                    setSelectedSchema(null);
                    setDiscovered([]);
                    setJobId(null);
                    setTables([]);
                  }}
                  schemas={schemas}
                  selectedSchema={selectedSchema}
                  onSelectSchema={setSelectedSchema}
                  onListSchemas={handleListSchemas}
                  onDiscover={handleDiscover}
                  onListObjects={handleListObjects}
                  discovering={discovering}
                  listingSchemas={listingSchemas}
                  tablesAndViews={tables}
                />
              </div>

              {error ? (
                <div className="hairline p-3 text-xs font-mono text-flag bg-card">
                  {error}
                </div>
              ) : null}

              <div className="bg-card hairline p-3">
                <p className="text-[10px] font-mono uppercase tracking-wider text-mute mb-2">
                  recent calls
                </p>
                <ul className="font-mono text-[11px] space-y-0.5">
                  {log.length === 0 ? (
                    <li className="text-mute">— no calls yet —</li>
                  ) : (
                    log.map((l) => (
                      <li key={l.at} className="flex items-center gap-2">
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            l.ok ? "bg-accent" : "bg-flag"
                          }`}
                        />
                        <span className={l.ok ? "text-ink" : "text-flag"}>
                          {l.label}
                        </span>
                      </li>
                    ))
                  )}
                </ul>
              </div>
            </aside>

        
<section className="lg:col-span-2">
  {tab === "dashboard" ? (
    <div className="space-y-6">
      <div className="rounded-3xl border border-pink-200 bg-gradient-to-br from-pink-50 via-white to-pink-100 p-8 shadow-sm">
        <p className="text-sm font-semibold uppercase tracking-widest text-pink-600">
          Welcome to your workspace
        </p>

        <h2 className="mt-3 text-3xl font-bold text-pink-900">
          Dashboard
        </h2>

        <p className="mt-2 text-pink-700">
          Discover, review, and anonymize sensitive database information
          in one secure workspace.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-2xl border border-pink-200 bg-white p-6 shadow-sm">
          <p className="text-sm text-pink-600">Databases</p>
          <p className="mt-3 text-3xl font-bold text-pink-900">
            {databases.length}
          </p>
          <p className="mt-2 text-sm text-gray-500">
            Databases found on the connected server
          </p>
        </div>

        <div className="rounded-2xl border border-pink-200 bg-white p-6 shadow-sm">
          <p className="text-sm text-pink-600">Discovered Tables</p>
          <p className="mt-3 text-3xl font-bold text-pink-900">
            {tables.length}
          </p>
          <p className="mt-2 text-sm text-gray-500">
            Tables and views currently loaded
          </p>
        </div>

        <div className="rounded-2xl border border-pink-200 bg-white p-6 shadow-sm">
          <p className="text-sm text-pink-600">Pending Approvals</p>
          <p className="mt-3 text-3xl font-bold text-pink-900">
            {pendingApprovals ?? "—"}
          </p>
          <p className="mt-2 text-sm text-gray-500">
            {jobId
              ? "Detected sensitive fields awaiting review"
              : "Run a discovery to create a review queue"}
          </p>
          <button
            onClick={() => setTab("approval")}
            className="btn-ghost mt-4"
          >
            Open Approval Center →
          </button>
        </div>

        <div className="rounded-2xl border border-pink-200 bg-white p-6 shadow-sm">
          <p className="text-sm text-pink-600">Anonymization</p>
          <p className="mt-3 text-2xl font-bold capitalize text-pink-900">
            {workflowPhase ? workflowPhase.split("_").join(" ") : "—"}
          </p>
          <p className="mt-2 text-sm text-gray-500">
            {jobId
              ? `${approvalCount ?? 0} approved columns · current workflow stage`
              : "Check execution status and history"}
          </p>
          <button
            onClick={() => setTab("execution")}
            className="btn-ghost mt-4"
          >
            Open Execution Monitor →
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-pink-200 bg-white p-6 shadow-sm">
        <h3 className="text-lg font-bold text-pink-900">
          Anonymization Workflow
        </h3>

        <p className="mt-2 text-sm text-gray-500">
          Follow these steps to process your database safely.
        </p>

        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
          {[
            "01 · Connect",
            "02 · Discover",
            "03 · Classify",
            "04 · Approve",
            "05 · Backup",
            "06 · Anonymize",
          ].map((step) => (
            <div
              key={step}
              className="rounded-xl bg-pink-50 px-4 py-3 text-sm font-medium text-pink-800"
            >
              {step}
            </div>
          ))}
        </div>

        <button
          onClick={() => setTab("workflow")}
          className="btn-accent mt-5"
        >
          View Full Workflow →
        </button>
      </div>
    </div>
  ) : tab === "schema" ? (
    <SchemaViewer
      tables={discovered}
      jobId={jobId}
      conn={conn}
      selectedDatabase={selectedDatabase}
    />
  ) : tab === "approval" ? (
    <ApprovalCenter jobId={jobId} conn={conn} />
  ) : tab === "backup" ? (
    <BackupCenter
      conn={conn}
      selectedDatabase={selectedDatabase}
      jobId={jobId}
    />
  ) : tab === "execution" ? (
    <ExecutionMonitor
      jobId={jobId}
      conn={conn}
      selectedDatabase={selectedDatabase}
    />
  ) : tab === "audit" ? (
    <AuditLogView jobId={jobId} />
  ) : tab === "workflow" ? (
    <WorkflowView
      jobId={jobId}
      conn={conn}
      selectedDatabase={selectedDatabase}
    />
  ) : tab === "settings" ? (
    <SettingsView />
  ) : tab === "users" && isAdmin ? (
    <UsersAdminTab />
  ) : tab === "servers" ? (
    <ServersTab
      activeServerId={activeServer?.id}
      onActivate={(s) => {
        setConn({ ...conn, host: s.host, port: s.port });
        setActiveServer({
          id: s.id,
          name: s.name,
          host: s.host,
          port: s.port,
        });
        setTab("schema");
      }}
    />
  ) : null}
</section>

          </div>
        </div>
      </main>

      <footer className="hairline-t">
        <div className="mx-auto max-w-7xl px-6 py-3 text-[11px] font-mono text-mute flex items-center gap-3">
          <span>phases 3–13 · read-only + approval + backup + masking + update · audit</span>
          <span className="text-rule">·</span>
          <span>no DDL by default · parameterized UPDATEs · FK-referential integrity</span>
          <span className="ml-auto">
            targets: 127.0.0.1:8765 (proxied via vite) · ollama: 11434
          </span>
        </div>
      </footer>
    </div>
  );
}

function UsersAdminTab() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    setBusy(true);
    try {
      const req: CreateUserRequest = { username, password, role };
      const u = await api.createUser(req);
      setMsg(`Created ${u.username} (id=${u.id.slice(0, 8)}, role=${u.role})`);
      setUsername("");
      setPassword("");
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Users</p>
        <p className="text-xs text-mute">admin-only · creates a new user with the chosen role</p>
      </header>
      <form onSubmit={submit} className="hairline p-4 space-y-3">
        <label className="block text-[11px] font-mono">
          <span className="text-mute">username</span>
          <input className="input mt-1" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="block text-[11px] font-mono">
          <span className="text-mute">password (min 8 chars)</span>
          <input
            className="input mt-1"
            type="password"
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <label className="block text-[11px] font-mono">
          <span className="text-mute">role</span>
          <select className="input mt-1" value={role} onChange={(e) => setRole(e.target.value as "user" | "admin")}>
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <button type="submit" className="btn-accent" disabled={busy}>
          {busy ? "Creating…" : "Create user"}
        </button>
        {msg ? (
          <div className="hairline p-2 text-[11px] font-mono">{msg}</div>
        ) : null}
      </form>
    </div>
  );
}

export function App() {
  return (
    <AuthProvider>
      <Inner />
    </AuthProvider>
  );
}
