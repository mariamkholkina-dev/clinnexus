/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // App Router включён по умолчанию в Next.js 13+,
  // экспериментальный флаг appDir больше не нужен.
  // Включение standalone режима для оптимизации Docker образа
  output: process.env.NODE_ENV === 'production' ? 'standalone' : undefined,
  // Настройка для работы через reverse proxy
  trailingSlash: false,
  // Отключение оптимизации изображений, если не используется
  images: {
    unoptimized: false,
  },
};

export default nextConfig;


