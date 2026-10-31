import type { NextConfig } from "next";

const defaultApiUrl =
  process.env.NODE_ENV === "production"
    ? "https://verba-english-9.onrender.com"
    : "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_API_URL: (process.env.NEXT_PUBLIC_API_URL || defaultApiUrl).trim().replace(/\/+$/, ""),
  },
  images: {
    remotePatterns: [{ protocol: "https", hostname: "res.cloudinary.com" }],
  },
};

export default nextConfig;
