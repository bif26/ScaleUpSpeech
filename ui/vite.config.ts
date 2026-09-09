import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [tailwindcss(), sveltekit()],
  server: {
    // Proxy API calls to the manager during dev so the UI doesn't need to
    // know it's talking to a different port.
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/manager': 'http://127.0.0.1:8765',
      '/ws': {
        target: 'ws://127.0.0.1:8765',
        ws: true,
      },
    },
  },
});
