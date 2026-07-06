import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  envDir: '../', // adjust if the frontend folder is nested deeper than one level from repo root
  build: {
    outDir: 'build', // Compiles exactly into the folder Nginx reads!
  }
});