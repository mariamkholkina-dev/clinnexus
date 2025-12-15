interface ImpactPageProps {
  params: { studyId: string };
}

export default function ImpactPage({ params }: ImpactPageProps) {
  const { studyId } = params;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Импакт и задачи по исследованию #{studyId}</h1>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded border bg-white p-4 text-sm">
          <h2 className="mb-2 text-sm font-semibold">Impact items</h2>
          <p className="text-xs text-slate-600">
            Здесь будет список ImpactItems, связанных с ChangeEvents и Tasks.
          </p>
        </div>
        <div className="rounded border bg-white p-4 text-sm">
          <h2 className="mb-2 text-sm font-semibold">Tasks</h2>
          <p className="text-xs text-slate-600">
            Список задач по внедрению изменений (RBAC, audit-friendly).
          </p>
          <div className="mt-2 text-xs text-slate-500">
            API: <code>GET /impact?study_id={studyId}</code>
          </div>
        </div>
      </div>
    </div>
  );
}


