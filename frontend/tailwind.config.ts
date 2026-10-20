
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#493747",
        paper: "#FFF7FB",
        card: "#FFFFFF",
        rule: "#F0DFEA",
        accent: "#DB2777",
        flag: "#BE123C",
        mute: "#8B7788",
      },
      fontFamily: {
        display: ['"IBM Plex Serif"', "ui-serif", "Georgia", "serif"],
        body: ['"IBM Plex Sans"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: [
          '"JetBrains Mono"',
          "ui-monospace",
          "SFMono-Regular",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};

export default config;
