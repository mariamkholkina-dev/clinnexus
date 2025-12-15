interface KbPageProps {
  params: { studyId: string };
}

export default function KbPage({ params }: KbPageProps) {
  const { studyId } = params;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">База знаний исследования #{studyId}</h1>
      <div className="rounded border bg-white p-4 text-sm text-slate-600">
        Здесь будет список фактов исследования с привязкой к якорям (fact_evidence).
        <div className="mt-2 text-xs text-slate-500">
          API: <code>GET /studies/{studyId}/facts</code>
        </div>
      </div>
    </div>
  );
}


