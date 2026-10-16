import { useEffect, useState } from "react";
import { api } from "../lib/api";

type SettingsData = {
  llm_provider: string;
  ollama_model: string;
  ollama_base_url: string;
  openai_model: string;
  openai_base_url: string;
  backup_dir: string;
  backup_keep: number;
  safe_update_batch_size: number;
  credential_encryption_key_set: boolean;
};

export function SettingsView() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSettings().then(setSettings).catch((e) => setError(String(e)));
  }, []);

  if (error) return <p className="text-red-500 text-sm p-4">{error}</p>;
  if (!settings) return <p className="text-mute text-sm p-4">Loading settings...</p>;

  const rows: Array<[string, string]> = [
    ["LLM provider", settings.llm_provider],
    ["Ollama model", settings.ollama_model],
    ["Ollama base URL", settings.ollama_base_url],
    ["OpenAI model", settings.openai_model],
    ["OpenAI base URL", settings.openai_base_url],
    ["Backup dir", settings.backup_dir],
    ["Backup retention (keep)", String(settings.backup_keep)],
    ["Safe update batch size", String(settings.safe_update_batch_size)],
    ["Encryption key configured", settings.credential_encryption_key_set ? "Yes" : "No"],
  ];

  return (
    <div className="space-y-3">
      <header className="flex items-center gap-3 hairline-b pb-3">
        <p className="font-display text-lg text-ink">Settings</p>
        <p className="text-xs text-mute">read-only view · configure via .env</p>
      </header>
      <div className="hairline">
        <table className="w-full font-mono text-xs">
          <thead>
            <tr className="text-left text-mute">
              <th className="px-4 py-2 font-normal w-64">key</th>
              <th className="px-2 py-2 font-normal">value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([k, v]) => (
              <tr key={k} className="hairline-t">
                <td className="px-4 py-2 text-ink">{k}</td>
                <td className="px-2 py-2 text-ink">{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] font-mono text-mute">
        Edit <code className="text-ink">backend/.env</code> and restart the
        API to change these values.
      </p>
    </div>
  );
}
