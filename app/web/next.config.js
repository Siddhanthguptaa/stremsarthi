/*
 * Adapted from Saximn/privastream (Apache-2.0), src/web/frontend/next.config.js. See LICENSE.privastream.
 * Removed the mediasoup/socket.io bundle fallbacks; added baseline security headers.
 */
/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          // Camera is needed on /enrol and the microphone only for the optional level test.
          { key: "Permissions-Policy", value: "camera=(self), microphone=(self), geolocation=()" },
        ],
      },
    ]
  },
}

module.exports = nextConfig
