import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import pkg from './package.json';

// base './' makes the build work from any path: GitHub Pages
// (https://<user>.github.io/CO2-Response/), a local folder, or `vite preview`.
export default defineConfig({
  base: './',
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
  },
});
