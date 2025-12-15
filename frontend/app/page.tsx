import Link from "next/link";

export default function HomePage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">ClinNexus Evidence-first</h1>
      <p className="text-sm text-slate-600">
        Минимальный интерфейс для работы с исследованиями, документами и фактами.
      </p>
      <Link
        href="/studies"
        className="inline-flex rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
      >
        Перейти к исследованиям
      </Link>
    </div>
  );
}


