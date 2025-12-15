import Link from "next/link";

const studies = [
  { id: 1, name: "Demo Study 1" },
  { id: 2, name: "Demo Study 2" },
];

export default function StudiesPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Исследования</h1>
      <ul className="divide-y rounded border bg-white">
        {studies.map((study) => (
          <li key={study.id} className="flex items-center justify-between px-4 py-3">
            <div>
              <div className="font-medium">{study.name}</div>
              <div className="text-xs text-slate-500">ID: {study.id}</div>
            </div>
            <Link
              href={`/studies/${study.id}`}
              className="text-sm font-medium text-blue-600 hover:underline"
            >
              Открыть
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}


