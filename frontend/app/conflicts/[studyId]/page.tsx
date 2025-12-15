interface ConflictsPageProps {
  params: { studyId: string };
}

export default function ConflictsPage({ params }: ConflictsPageProps) {
  const { studyId } = params;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Конфликты исследования #{studyId}</h1>
      <div className="rounded border bg-white p-4 text-sm">
        <div className="mb-2 text-xs text-slate-500">
          API: <code>GET /conflicts?study_id={studyId}</code>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="border-b bg-slate-50">
                <th className="px-2 py-1 text-left font-medium">ID</th>
                <th className="px-2 py-1 text-left font-medium">Статус</th>
                <th className="px-2 py-1 text-left font-medium">Описание</th>
                <th className="px-2 py-1 text-left font-medium">Левая сторона</th>
                <th className="px-2 py-1 text-left font-medium">Правая сторона</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className="px-2 py-1">—</td>
                <td className="px-2 py-1">open</td>
                <td className="px-2 py-1 text-slate-500">Пока нет данных</td>
                <td className="px-2 py-1 text-slate-400">anchor: …</td>
                <td className="px-2 py-1 text-slate-400">anchor: …</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}


