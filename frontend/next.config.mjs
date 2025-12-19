/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // App Router включён по умолчанию в Next.js 13+,
  // экспериментальный флаг appDir больше не нужен.
  // Включение standalone режима для оптимизации Docker образа
  output: process.env.NODE_ENV === 'production' ? 'standalone' : undefined,
};

export default nextConfig;


