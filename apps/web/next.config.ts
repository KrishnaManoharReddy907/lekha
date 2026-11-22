import type { NextConfig } from "next";

// Trim whitespace/newlines and a trailing slash: env values pasted through CLIs often carry them,
// and an invalid rewrite destination fails the whole build.
const API_URL = (process.env.LEKHA_API_URL ?? "http://localhost:8000").trim().replace(/\/+$/, "");

const config: NextConfig = {
  // The browser only ever talks to this origin; /api is forwarded to the Python service.
  // That keeps the session cookie first-party and avoids CORS entirely.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API_URL}/api/:path*` },
      { source: "/health", destination: `${API_URL}/health` },
    ];
  },
  poweredByHeader: false,
  // Some API calls legitimately take longer than the 30 s default (multi-step copilot answers, notice packs).
  experimental: { proxyTimeout: 120_000 },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default config;
