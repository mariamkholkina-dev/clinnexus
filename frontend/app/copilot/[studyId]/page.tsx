interface CopilotPageProps {
  params: { studyId: string };
}

export default function CopilotPage({ params }: CopilotPageProps) {
  const { studyId } = params;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Копилот для исследования #{studyId}</h1>
      <div className="grid gap-4 md:grid-cols-[2fr,1fr]">
        <div className="space-y-3">
          <label className="block text-xs font-medium text-slate-600">
            Ключ секции
            <input
              className="mt-1 w-full rounded border px-2 py-1 text-sm"
              defaultValue="introduction"
            />
          </label>
          <label className="block text-xs font-medium text-slate-600">
            Сгенерированный текст
            <textarea className="mt-1 h-40 w-full rounded border px-2 py-1 text-sm" />
          </label>
          <button className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
            Сгенерировать секцию
          </button>
          <div className="text-xs text-slate-500">
            Вызов API: <code>POST /generate/section</code>
          </div>
        </div>
        <div className="space-y-3">
          <h2 className="text-sm font-semibold">QC отчёт</h2>
          <div className="rounded border bg-white p-3 text-xs text-slate-600">
            QCReport будет отображаться здесь (items, severity, anchor_ids).
          </div>
        </div>
      </div>
    </div>
  );
}


