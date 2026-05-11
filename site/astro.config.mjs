import { defineConfig } from "astro/config";

export default defineConfig({
  site: "https://tend-src.com",
  base: process.env.SITE_BASE ?? "/",
  server: {
    host: true,
  },
});
