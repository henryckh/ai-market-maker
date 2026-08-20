/** @type {import('next').NextConfig} */
const fs = require("fs");
const path = require("path");

// next dev only — Docker sets AIMM_API_KEY in the entrypoint from the shared volume.
if (process.env.NODE_ENV !== "production" && !process.env.AIMM_API_KEY) {
  try {
    const fromFile = fs
      .readFileSync(path.join(__dirname, "..", ".secrets", "api_key"), "utf8")
      .trim();
    if (fromFile) process.env.AIMM_API_KEY = fromFile;
  } catch {}
}

const nextConfig = {
  // We use Next route handlers (`app/api/*`) as lightweight proxies to the Python backend,
  // so we cannot use `output: "export"` (static export forbids dynamic routes).
  output: "standalone",
  trailingSlash: false,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
          {
            key: "Content-Security-Policy",
            value:
              "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
          },
        ],
      },
    ];
  },
};
module.exports = nextConfig;
