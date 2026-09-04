import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits .next/standalone with only the files the server needs, so the runtime
  // image does not carry node_modules.
  output: "standalone",

  turbopack: {
    root: process.cwd(),
  },

  async rewrites() {
    // NOTE: rewrites() is evaluated during `next build` and serialised into
    // routes-manifest.json -- it is NOT read at runtime. In Docker this value
    // comes from the BACKEND_ORIGIN build arg, so changing which backend the
    // image talks to requires a rebuild.
    //
    // A runtime-configurable alternative is a proxy.ts, but Next buffers the
    // request body when one is used (10MB by default), which would break clip
    // uploads well under the 500MB limit the API accepts.
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
