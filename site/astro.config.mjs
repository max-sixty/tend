import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://tend.dev",
  base: process.env.SITE_BASE ?? "/",
  server: {
    host: true,
  },
});
