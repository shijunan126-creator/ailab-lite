/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Preserve the loopback origin so /login remains an internal rewrite.
  skipProxyUrlNormalize: true,
  ...(process.env.AILAB_STANDALONE === "1" ? { output: "standalone" } : {}),
};

export default nextConfig;
