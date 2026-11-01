/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Preserve the loopback origin so /login remains an internal rewrite.
  skipProxyUrlNormalize: true,
  ...(process.env.AILAB_STANDALONE === "1" ? { output: "standalone" } : {}),
  // app/pdfjs reads these at runtime, so the standalone output must carry them.
  outputFileTracingIncludes: {
    "/pdfjs/[version]/[kind]/[name]": ["./node_modules/pdfjs-dist/package.json", "./node_modules/pdfjs-dist/{cmaps,standard_fonts,wasm,iccs}/**"],
  },
};

export default nextConfig;
