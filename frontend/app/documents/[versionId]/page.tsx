interface DocumentVersionPageProps {
  params: { versionId: string };
}

export default function DocumentVersionPage({ params }: DocumentVersionPageProps) {
  const { versionId } = params;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Документ версия #{versionId}</h1>
      <div className="grid gap-4 md:grid-cols-[2fr,1fr]">
        <div className="rounded border bg-white p-4">
          <div className="text-sm text-slate-500">Viewer заглушка (PDF/DOCX)</div>
          <div className="mt-4 h-64 rounded border border-dashed text-xs text-slate-400">
            {/* placeholder */}
          </div>
        </div>
        <div className="space-y-3">
          <h2 className="text-sm font-semibold">Anchors</h2>
          <div className="rounded border bg-white p-3 text-xs text-slate-600">
            Список якорей документа будет загружаться из API
            <code className="ml-1 text-[10px] text-slate-400">
              GET /document_versions/{versionId}/anchors
            </code>
          </div>
        </div>
      </div>
    </div>
  );
}


