import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev loop: `vite dev` proxies /api and /ws straight through to a real,
// separately-running `coscribe-web` process (default :8000, override
// with COSCRIBE_BACKEND) -- every feature here is built and manually
// verified against real WebSocket/REST traffic from day one, not mocks,
// matching this project's own "live-verify, don't assume" discipline
// (see runtime_lg/README.md). `ws: true` is required on the /ws proxy
// entry specifically -- Vite's http-proxy-middleware only upgrades
// WebSocket connections for paths that opt in.
const backend = process.env.COSCRIBE_BACKEND ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/ws": { target: backend, ws: true },
    },
  },
  // Cutover: `npm run build`'s output lands directly in
  // coscribe/web/app.py's real STATIC_DIR, replacing the old
  // vanilla-JS app.js/index.html/style.css (deleted from git -- this
  // directory is now a build artifact, gitignored). `base: "/static/"`
  // matches app.py's `app.mount("/static", ...)` exactly -- GET "/"
  // serves this directory's index.html directly (FileResponse, not
  // through the mount), so its asset references need the "/static/"
  // prefix to resolve correctly, and Vite's own base setting is what
  // controls that.
  base: "/static/",
  build: {
    outDir: "../src/coscribe/web/static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Fixed (non-hashed) filenames: coscribe/web/app.py's
        // _NoCacheStaticFiles already sends Cache-Control: no-store on
        // everything under /static/, specifically because these files
        // change on every `git pull` + restart with no other cache-bust
        // mechanism -- content hashing here would be redundant, and
        // tests/test_web.py asserts on the literal path /static/app.js.
        entryFileNames: "app.js",
        chunkFileNames: "app-[name].js",
        assetFileNames: (assetInfo) => (assetInfo.names?.[0]?.endsWith(".css") ? "style.css" : "assets/[name][extname]"),
      },
    },
  },
});
