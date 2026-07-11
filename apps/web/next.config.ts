import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // /capabilities reads docs/capabilities with fs at request time (compose
  // ro-mount, or repo-relative fallback in dev). Dynamic fs paths make the
  // tracer conservatively sweep the whole project dir into the standalone
  // output; none of those project files are needed at runtime, so exclude
  // them (node_modules tracing is unaffected).
  outputFileTracingExcludes: {
    "/capabilities": [
      "./src/**/*",
      "./public/**/*",
      "./*.md",
      "./*.ts",
      "./*.mjs",
      "./*.json",
      "./*.tsbuildinfo",
      "./Dockerfile",
    ],
  },
};

export default nextConfig;
