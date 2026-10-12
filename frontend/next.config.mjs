/** @type {import('next').NextConfig} */
// Static export: the FastAPI server serves this UI and the API from one origin (one free container = whole app).
// `npm run dev` still works against a separate API at NEXT_PUBLIC_API_URL (default http://localhost:8000).
const nextConfig = { output: "export", trailingSlash: true, images: { unoptimized: true } };
export default nextConfig;
