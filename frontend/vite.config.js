import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/auth': 'http://localhost:5000',
      '/integrations': 'http://localhost:5000',
      '/health': 'http://localhost:5000',
      '/usage': 'http://localhost:5000',
      '/upload': 'http://localhost:5000',
      '/analyze': 'http://localhost:5000',
      '/followup': 'http://localhost:5000',
      '/video': 'http://localhost:5000'
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
});
