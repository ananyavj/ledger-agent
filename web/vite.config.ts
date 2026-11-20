import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the browser calls /api/... on the Vite server (port 5173), which
// forwards to FastAPI on 8000. Same origin for the browser = no CORS setup needed.
// In Docker, nginx does the same job (see nginx.conf).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
