/** @type {import('next').NextConfig} */
const distDir = process.env.NEXT_DIST_DIR;

const nextConfig = {
  ...(distDir ? { distDir } : {}),
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8001/api/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
