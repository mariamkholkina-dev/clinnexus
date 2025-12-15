import Link from "next/link";

interface StudyPageProps {
  params: { id: string };
}

export default function StudyPage({ params }: StudyPageProps) {
  const { id } = params;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Исследование #{id}</h1>
      <div className="grid gap-3 md:grid-cols-2">
        <SectionCard
          title="Документы и версии"
          description="Загрузка и просмотр версий протокола и других документов."
          href={`/documents/${id}`}
        />
        <SectionCard
          title="База знаний (факты)"
          description="Факты исследования и ссылки на якоря."
          href={`/kb/${id}`}
        />
        <SectionCard
          title="Копилот секций"
          description="Генерация и QC текстов разделов протокола."
          href={`/copilot/${id}`}
        />
        <SectionCard
          title="Конфликты"
          description="Обнаруженные противоречия и расхождения."
          href={`/conflicts/${id}`}
        />
        <SectionCard
          title="Импакт"
          description="Элементы влияния и задачи по изменениям."
          href={`/impact/${id}`}
        />
      </div>
    </div>
  );
}

interface SectionCardProps {
  title: string;
  description: string;
  href: string;
}

function SectionCard({ title, description, href }: SectionCardProps) {
  return (
    <Link
      href={href}
      className="flex flex-col justify-between rounded border bg-white p-4 text-sm hover:border-blue-500 hover:shadow-sm"
    >
      <div>
        <div className="font-medium">{title}</div>
        <p className="mt-1 text-xs text-slate-600">{description}</p>
      </div>
      <span className="mt-3 text-xs font-medium text-blue-600">Открыть →</span>
    </Link>
  );
}


