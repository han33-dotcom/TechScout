import { defineConfig } from 'vite';

const api = process.env.TECHSCOUT_API_ORIGIN || 'http://127.0.0.1:8000';

export default defineConfig({
  server: {
    proxy: {
      '/api': api,
      '/health': api,
      '/sources': api,
    },
  },
});
