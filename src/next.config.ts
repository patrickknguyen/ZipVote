import path from "node:path";
import type { NextConfig } from "next";

// Static export for GitHub Pages, served at patrickknguyen.github.io/ZipVote
const nextConfig: NextConfig = {
  output: "export",
  basePath: "/ZipVote",
  trailingSlash: true, // /quiz/ → quiz/index.html, which Pages serves directly
  // The generated questions live in ../data/processed (next to the Python
  // pipeline). Turbopack only reads files inside its root, so widen it to
  // the whole ZipVote folder or the questions are silently left out.
  turbopack: { root: path.resolve(__dirname, "..") },
  images: {
    // Pages has no image server, so load headshots as-is
    unoptimized: true,
    remotePatterns: [
      {
        // Wikipedia / Wikimedia Commons headshots
        protocol: "https",
        hostname: "upload.wikimedia.org",
        pathname: "/wikipedia/commons/**",
      },
    ],
  },
};

export default nextConfig;
