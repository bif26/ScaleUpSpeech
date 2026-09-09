import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({
      // SPA fallback so client-side routing works when served by FastAPI
      fallback: 'index.html',
      pages: 'build',
      assets: 'build',
      precompress: false,
      strict: false,
    }),
    alias: {
      $lib: 'src/lib',
      $components: 'src/lib/components',
    },
  },
};

export default config;
