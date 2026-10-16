import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#0F172A",
        paper: "#F8F7F4",
        card: "#FFFFFF",
        rule: "#E5E1D8",
        accent: "#0EA5A4",
        flag: "#B45309",
        mute: "#64748B",
      },
      fontFamily: {
        display: ['"IBM Plex Serif"', "ui-serif", "Georgia", "serif"],
        body: ['"IBM Plex Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
