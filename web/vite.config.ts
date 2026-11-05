import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// base './' makes the build work from any path: GitHub Pages
// (https://<user>.github.io/CO2-Response/), a local folder, or `vite preview`.
export default defineConfig({
  base: './',
  plugins: [react()],
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
  },
});
