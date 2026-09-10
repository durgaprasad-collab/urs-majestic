import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two build targets from one source:
//  - `npm run build`     -> dist/     base "/"      (wrapped by Capacitor, served from the app's own root)
//  - `npm run build:web` -> dist-web/ base "/staff/" (hosted at admin.ursmajestic.com/staff for the iPhone owner)
export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE_PATH || "/",
  build: {
    outDir: process.env.VITE_OUT_DIR || "dist",
  },
  server: { port: 5174 },
});
