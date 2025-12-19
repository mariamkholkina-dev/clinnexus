/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // App Router включён по умолчанию в Next.js 13+,
  // экспериментальный флаг appDir больше не нужен.
  // Временно отключен standalone режим из-за проблем со статическими файлами
  // output: process.env.NODE_ENV === 'production' ? 'standalone' : undefined,
  // Настройка для работы через reverse proxy
  trailingSlash: false,
  // Отключение оптимизации изображений, если не используется
  images: {
    unoptimized: false,
  },
  // Настройка для работы за прокси - использовать относительные пути для статики
  // Это предотвратит генерацию абсолютных URL с IP-адресом
  assetPrefix: process.env.NODE_ENV === 'production' ? '' : undefined,
};

export default nextConfig;


