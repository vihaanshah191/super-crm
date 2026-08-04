import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // Dev-only: allows the dev server to serve _next/* resources when accessed
  // via 127.0.0.1 instead of localhost (e.g. from automated browser testing).
  // Has no effect on `next build` / production.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
