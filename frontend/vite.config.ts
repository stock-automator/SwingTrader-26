import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 0.0.0.0 (not just localhost) so a phone/tablet on the same LAN or a
    // NordVPN Meshnet/Tailscale peer can reach the dev server at this
    // machine's Meshnet/LAN IP, not only from the machine itself. Backend
    // CORS allows those same private-network origins - see
    // `backend/app/config.py`'s `cors_origin_regex`.
    host: "0.0.0.0",
    port: 5173,
  },
})
