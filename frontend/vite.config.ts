import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    host: "127.0.0.1",
    proxy: {
      "/health": "http://127.0.0.1:8765",
      "/auth": "http://127.0.0.1:8765",
      "/users": "http://127.0.0.1:8765",
      "/servers": "http://127.0.0.1:8765",
      "/discovery": "http://127.0.0.1:8765",
      "/schema": "http://127.0.0.1:8765",
      "/sampling": "http://127.0.0.1:8765",
      "/classification": "http://127.0.0.1:8765",
      "/approvals": "http://127.0.0.1:8765",
      "/backups": "http://127.0.0.1:8765",
      "/masking": "http://127.0.0.1:8765",
      "/updates": "http://127.0.0.1:8765",
      "/audit": "http://127.0.0.1:8765",
      "/workflow": "http://127.0.0.1:8765",
      "/settings": "http://127.0.0.1:8765",
    },
  },
});
