import type { NextConfig } from "next";

// Next buffers the ENTIRE request body of anything it proxies -- rewrites
// included, not just proxy.ts -- and the default cap is 10MB. Clip uploads are
// multipart with several files, so a routine upload silently exceeded it: the
// body was truncated and the upstream connection reset, surfacing as an opaque
// failure with no useful message.
//
// This must stay in step with MAX_UPLOAD_BYTES on the backend, which enforces
// the same budget and returns a clear 413. Both read the same env var; the
// backend at runtime, Next at build time.
const MAX_UPLOAD_BYTES = Number(process.env.MAX_UPLOAD_BYTES ?? 536870912);

const nextConfig: NextConfig = {
  // Emits .next/standalone with only the files the server needs, so the runtime
  // image does not carry node_modules.
  output: "standalone",

  turbopack: {
    root: process.cwd(),
  },

  experimental: {
    proxyClientMaxBodySize: MAX_UPLOAD_BYTES,
  },

  async rewrites() {
    // NOTE: rewrites() is evaluated during `next build` and serialised into
    // routes-manifest.json -- it is NOT read at runtime. In Docker this value
    // comes from the BACKEND_ORIGIN build arg, so changing which backend the
    // image talks to requires a rebuild.
    const backendOrigin =
      process.env.FASTAPI_PUBLIC_URL ?? "http://127.0.0.1:8000";

    return [
      {
        source: "/api/backend/:path*",
        destination: `${backendOrigin}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
