/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  ...(process.env.AILAB_STANDALONE === "1" ? { output: "standalone" } : {}),
};

export default nextConfig;
