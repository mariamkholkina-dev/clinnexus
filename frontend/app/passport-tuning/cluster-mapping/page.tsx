"use client"

import { useState, useEffect, useMemo, useCallback, useRef, Suspense } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Progress } from "@/components/ui/progress"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"
import {
  Search,
  ChevronLeft,
  ChevronRight,
  Save,
  ArrowRight,
  Trash2,
  Download,
  Target,
  FileText,
  AlertCircle,
  CheckCircle2,
  Info,
  Loader2,
  ChevronDown,
  ChevronUp,
} from "lucide-react"

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "/api"

// Types
type Cluster = {
  cluster_id: string | number
  top_titles_ru: string[]
  top_titles_en?: string[]
  examples: { section_path: string; heading_text_raw: string }[]
  stats?: { content_type_distribution?: Record<string, number>; avg_total_chars?: number }
  candidate_section_1?: { section_key: string; title_ru: string; score: number }
  candidate_section_2?: { section_key: string; title_ru: string; score: number }
  candidate_section_3?: { section_key: string; title_ru: string; score: number }
  default_section?: string
  is_mapped?: boolean
}

type DocType = "protocol" | "csr" | "sap" | "tfl" | "ib" | "icf" | "other"

type MappingMode = "single" | "ambiguous" | "skip" | "needs_split"

type MappingEntry = {
  doc_type: DocType
  section_key: string
  title_ru?: string
  mapping_mode?: MappingMode
  notes?: string
}

type Mapping = Record<string, MappingEntry>

type AlertType = "success" | "error" | "warning" | null

// Protocol section keys for autocomplete
const protocolSectionKeys = [
  "protocol.background_rationale",
  "protocol.title_page",
  "protocol.study_design",
  "protocol.eligibility.inclusion",
  "protocol.schedule_of_activities",
  "protocol.objectives",
  "protocol.synopsis",
  "protocol.endpoints",
  "protocol.population",
  "protocol.eligibility.non_inclusion",
  "protocol.eligibility.exclusion",
  "protocol.ip_handling",
  "protocol.dosing_administration",
  "protocol.concomitant_therapy",
  "protocol.contraception",
  "protocol.study_plan",
  "protocol.procedures_visits",
  "protocol.soa",
  "protocol.efficacy_assessment",
  "protocol.safety_assessment",
  "protocol.safety.ae_reporting",
  "protocol.pregnancy",
  "protocol.pk",
  "protocol.bioanalytical_method",
  "protocol.sample_handling",
  "protocol.statistics",
  "protocol.sample_size",
  "protocol.interim_analysis",
  "protocol.stat_plan_deviations",
  "protocol.quality_assurance",
  "protocol.monitoring",
  "protocol.data_capture_crf",
  "protocol.ethics",
  "protocol.informed_consent",
  "protocol.legal_aspects",
  "protocol.document_storage",
  "protocol.publication_plan",
  "protocol.regulatory_docs",
  "protocol.references",
  "protocol.appendices",
  "protocol.discontinuation",
  "protocol.subject_withdrawal_followup",
  "protocol.subject_replacement",
  "protocol.randomization_code_handling",
  "protocol.ip_storage_accountability",
  "protocol.ip_labeling",
]

const csrSectionKeys = ["csr.synopsis", "csr.methods.study_design"]

const sapSectionKeys = ["sap.analysis_sets"]

const sectionKeysByDocType: Record<DocType, string[]> = {
  protocol: protocolSectionKeys,
  csr: csrSectionKeys,
  sap: sapSectionKeys,
  tfl: ["tfl.table", "tfl.figure", "tfl.listing"],
  ib: ["ib.introduction", "ib.safety", "ib.efficacy"],
  icf: ["icf.introduction", "icf.procedures", "icf.risks"],
  other: ["other.section"],
}

function ClusterMappingContent() {
  const router = useRouter()
  const searchParams = useSearchParams()

  // State
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [mapping, setMapping] = useState<Mapping>({})
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState("")
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState("")
  const [filter, setFilter] = useState<"all" | "mapped" | "unmapped">("all")
  const [page, setPage] = useState(1)
  const pageSize = 50
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  // Cache for pages
  const pageCache = useRef<Map<string, { items: Cluster[]; total: number; timestamp: number }>>(new Map())
  const CACHE_TTL = 5 * 60 * 1000 // 5 minutes
  const loadingPages = useRef<Set<string>>(new Set())

  // Form state
  const [docType, setDocType] = useState<DocType>("protocol")
  const [sectionKey, setSectionKey] = useState("")
  const [titleRu, setTitleRu] = useState("")
  const [mappingMode, setMappingMode] = useState<MappingMode>("single")
  const [notes, setNotes] = useState("")

  // Alert state
  const [alert, setAlert] = useState<{ type: AlertType; message: string }>({
    type: null,
    message: "",
  })

  // UI state for collapsible sections
  const [expandedSections, setExpandedSections] = useState<{
    ru: boolean
    en: boolean
    examples: boolean
    stats: boolean
  }>({
    ru: true,
    en: true,
    examples: true,
    stats: true,
  })

  // Taxonomy state
  type TaxonomyNode = {
    section_key: string
    title_ru: string
    parent_section_key: string | null
    is_narrow: boolean
    expected_content?: any
  }
  type TaxonomyAlias = {
    alias_key: string
    canonical_key: string
    reason?: string
  }
  type TaxonomyRelated = {
    a_section_key: string
    b_section_key: string
    reason?: string
  }
  type TaxonomyData = {
    nodes: TaxonomyNode[]
    aliases: TaxonomyAlias[]
    related: TaxonomyRelated[]
  }
  const [taxonomyData, setTaxonomyData] = useState<TaxonomyData | null>(null)
  const [taxonomyLoading, setTaxonomyLoading] = useState(false)

  // Mobile tabs state
  const [mobileTab, setMobileTab] = useState<"clusters" | "details" | "mapping">("clusters")

  // Debounce search query
  const prevSearchQueryRef = useRef(searchQuery)
  useEffect(() => {
    const timer = setTimeout(() => {
      const searchChanged = searchQuery !== prevSearchQueryRef.current
      prevSearchQueryRef.current = searchQuery
      setDebouncedSearchQuery(searchQuery)
      if (searchChanged) {
        setPage(1) // Reset to first page on new search
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [searchQuery])

  // Load clusters from API with caching
  const loadClustersRef = useRef<(targetPage: number, search: string, useCache: boolean) => Promise<void>>()
  
  loadClustersRef.current = async (targetPage: number, search: string, useCache: boolean = true) => {
    const cacheKey = `${targetPage}-${search}`
    
    // Check cache first
    if (useCache) {
      const cached = pageCache.current.get(cacheKey)
      if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
        setClusters(cached.items)
        setTotal(cached.total)
        setLoading(false)
        return
      }
    }

    // Prevent duplicate requests
    if (loadingPages.current.has(cacheKey)) {
      return
    }

    try {
      loadingPages.current.add(cacheKey)
      setLoading(true)
      
      const params = new URLSearchParams({
        page: targetPage.toString(),
        page_size: pageSize.toString(),
      })
      if (search) {
        params.append("search", search)
      }
      
      const response = await fetch(`${API_BASE_URL}/passport-tuning/clusters?${params}`)
      if (!response.ok) {
        const errorText = await response.text()
        throw new Error(`Ошибка загрузки кластеров (${response.status}): ${errorText}`)
      }
      
      const data = await response.json()
      const items = data.items || []
      const total = data.total || 0
      
      // Cache the result
      pageCache.current.set(cacheKey, {
        items,
        total,
        timestamp: Date.now(),
      })
      
      // Clean old cache entries (keep only last 10 pages)
      if (pageCache.current.size > 10) {
        const entries = Array.from(pageCache.current.entries())
        entries.sort((a, b) => b[1].timestamp - a[1].timestamp)
        pageCache.current.clear()
        entries.slice(0, 10).forEach(([key, value]) => {
          pageCache.current.set(key, value)
        })
      }
      
      setClusters(items)
      setTotal(total)
      
      // Preload next page in background (only if this is the current page)
      const currentPage = page
      if (targetPage === currentPage && targetPage < Math.ceil(total / pageSize)) {
        const nextPageKey = `${targetPage + 1}-${search}`
        if (!pageCache.current.has(nextPageKey) && !loadingPages.current.has(nextPageKey)) {
          // Preload in background without blocking
          setTimeout(() => {
            loadClustersRef.current?.(targetPage + 1, search, false).catch(() => {
              // Ignore preload errors
            })
          }, 100)
        }
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Ошибка загрузки кластеров"
      setAlert({ type: "error", message: errorMessage })
      console.error("Ошибка загрузки кластеров:", err)
    } finally {
      setLoading(false)
      loadingPages.current.delete(cacheKey)
    }
  }

  // Load mapping from API
  const loadMapping = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/passport-tuning/mapping`)
      if (!response.ok) {
        throw new Error("Ошибка загрузки маппинга")
      }
      const data = await response.json()
      setMapping(data.mapping || {})
    } catch (err) {
      console.error("Ошибка загрузки маппинга:", err)
    }
  }, [])

  // Optimistic page switching - show cached data immediately
  useEffect(() => {
    const cacheKey = `${page}-${debouncedSearchQuery}`
    const cached = pageCache.current.get(cacheKey)
    
    if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
      // Show cached data immediately
      setClusters(cached.items)
      setTotal(cached.total)
      setLoading(false)
      // Still refresh in background to ensure data is up to date
      loadClustersRef.current?.(page, debouncedSearchQuery, false)
    } else {
      // Load from API
      loadClustersRef.current?.(page, debouncedSearchQuery, true)
    }
  }, [page, debouncedSearchQuery, pageSize])

  useEffect(() => {
    loadMapping()
  }, [loadMapping])

  // Load taxonomy when docType changes
  const loadTaxonomy = useCallback(async (docType: DocType) => {
    try {
      setTaxonomyLoading(true)
      const response = await fetch(`${API_BASE_URL}/passport-tuning/sections?doc_type=${docType}`)
      if (!response.ok) {
        throw new Error("Ошибка загрузки taxonomy")
      }
      const data = await response.json()
      setTaxonomyData(data)
    } catch (err) {
      console.error("Ошибка загрузки taxonomy:", err)
      setTaxonomyData(null)
    } finally {
      setTaxonomyLoading(false)
    }
  }, [])

  useEffect(() => {
    if (docType) {
      loadTaxonomy(docType)
    }
  }, [docType, loadTaxonomy])

  // Initialize from URL
  useEffect(() => {
    const clusterParam = searchParams.get("cluster")
    if (clusterParam) {
      setSelectedClusterId(clusterParam)
    }
  }, [searchParams])

  // Update mapping status
  useEffect(() => {
    setClusters((prev) =>
      prev.map((cluster) => {
        const mappingEntry = mapping[String(cluster.cluster_id)]
        return {
          ...cluster,
          is_mapped: mappingEntry !== undefined,
          mapping_mode: mappingEntry?.mapping_mode,
        }
      }),
    )
  }, [mapping])

  // Filtered and searched clusters (search is done server-side, but we filter by mapped status client-side)
  const filteredClusters = useMemo(() => {
    let result = clusters

    // Apply filter
    if (filter === "mapped") {
      // Размеченные = любые состояния кроме отсутствия записи
      result = result.filter((c) => mapping[String(c.cluster_id)] !== undefined)
    } else if (filter === "unmapped") {
      // Неразмеченные = нет записи в mapping
      result = result.filter((c) => mapping[String(c.cluster_id)] === undefined)
    }

    return result
  }, [clusters, filter, mapping])

  // Paginated clusters (pagination is done server-side)
  const paginatedClusters = filteredClusters

  const totalPages = Math.ceil(total / pageSize)

  // Counts
  const mappedCount = Object.keys(mapping).length
  const unmappedCount = total - mappedCount

  // Selected cluster
  const selectedCluster = clusters.find((c) => String(c.cluster_id) === selectedClusterId)

  // Auto-fill form when cluster is selected
  useEffect(() => {
    if (!selectedClusterId) return

    const existingMapping = mapping[selectedClusterId]
    if (existingMapping) {
      setDocType(existingMapping.doc_type)
      setSectionKey(existingMapping.section_key)
      setTitleRu(existingMapping.title_ru || "")
      setMappingMode(existingMapping.mapping_mode || "single")
      setNotes(existingMapping.notes || "")
    } else {
      // Try to get from candidates
      const cluster = clusters.find((c) => String(c.cluster_id) === selectedClusterId)
      if (cluster?.candidate_section_1) {
        const key = cluster.candidate_section_1.section_key
        const inferredDocType = key.split(".")[0] as DocType
        setDocType(inferredDocType || "protocol")
        setSectionKey(key)
        setTitleRu(cluster.candidate_section_1.title_ru)
      } else if (cluster?.default_section) {
        setSectionKey(cluster.default_section)
        setDocType("protocol")
        setTitleRu("")
      } else {
        setDocType("protocol")
        setSectionKey("")
        setTitleRu("")
      }
      // Дефолты для новых полей
      setMappingMode("single")
      setNotes("")
    }
  }, [selectedClusterId, mapping, clusters])

  // Handlers
  const handleSelectCluster = (clusterId: string | number) => {
    const clusterIdStr = String(clusterId)
    setSelectedClusterId(clusterIdStr)
    router.replace(`/passport-tuning/cluster-mapping?cluster=${clusterIdStr}`, { scroll: false })
    setAlert({ type: null, message: "" })
  }

  const handleSelectCandidate = (candidate: { section_key: string; title_ru: string }) => {
    const inferredDocType = candidate.section_key.split(".")[0] as DocType
    setDocType(inferredDocType || "protocol")
    setSectionKey(candidate.section_key)
    setTitleRu(candidate.title_ru)
  }

  const validateMapping = (): boolean => {
    // Для режима skip разрешаем пустые поля
    if (mappingMode === "skip") {
      return true
    }

    if (!sectionKey.trim()) {
      setAlert({ type: "warning", message: "Введите section_key" })
      return false
    }
    if (!sectionKey.startsWith(`${docType}.`)) {
      setAlert({
        type: "warning",
        message: `Предупреждение: section_key должен начинаться с "${docType}."`,
      })
      return true // Allow but warn
    }
    return true
  }

  const handleSave = async () => {
    if (!selectedClusterId) return
    if (!validateMapping()) return

    try {
      setSaving(true)
      setAlert({ type: null, message: "" })

      const newMapping = {
        ...mapping,
        [selectedClusterId]: {
          doc_type: docType,
          section_key: sectionKey,
          title_ru: titleRu || undefined,
          mapping_mode: mappingMode,
          notes: notes || undefined,
        },
      }

      const response = await fetch(`${API_BASE_URL}/passport-tuning/mapping`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newMapping),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail?.message || "Ошибка сохранения")
      }

      setMapping(newMapping)
      setAlert({ type: "success", message: "Сохранено успешно" })
      setTimeout(() => setAlert({ type: null, message: "" }), 3000)
    } catch (err) {
      setAlert({ type: "error", message: err instanceof Error ? err.message : "Ошибка сохранения" })
    } finally {
      setSaving(false)
    }
  }

  const handleSaveAndNext = async () => {
    if (!selectedClusterId) return
    if (!validateMapping()) return

    try {
      setSaving(true)
      setAlert({ type: null, message: "" })

      const newMapping = {
        ...mapping,
        [selectedClusterId]: {
          doc_type: docType,
          section_key: sectionKey,
          title_ru: titleRu || undefined,
          mapping_mode: mappingMode,
          notes: notes || undefined,
        },
      }

      const response = await fetch(`${API_BASE_URL}/passport-tuning/mapping`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(newMapping),
      })

      if (!response.ok) {
        const errorData = await response.json()
        throw new Error(errorData.detail?.message || "Ошибка сохранения")
      }

      setMapping(newMapping)

      // Find next unmapped cluster (неразмеченный = отсутствует запись в mapping)
      const currentIndex = clusters.findIndex((c) => String(c.cluster_id) === selectedClusterId)
      const nextUnmapped = clusters
        .slice(currentIndex + 1)
        .find((c) => !mapping[String(c.cluster_id)])

      if (nextUnmapped) {
        handleSelectCluster(nextUnmapped.cluster_id)
        setAlert({ type: "success", message: "Сохранено. Следующий кластер." })
        setTimeout(() => setAlert({ type: null, message: "" }), 3000)
      } else {
        setAlert({ type: "success", message: "Все кластеры размечены!" })
        setTimeout(() => setAlert({ type: null, message: "" }), 3000)
      }
    } catch (err) {
      setAlert({ type: "error", message: err instanceof Error ? err.message : "Ошибка сохранения" })
    } finally {
      setSaving(false)
    }
  }

  const handleClear = () => {
    setDocType("protocol")
    setSectionKey("")
    setTitleRu("")
    setMappingMode("single")
    setNotes("")
    setAlert({ type: null, message: "" })
  }

  const handleDownload = () => {
    window.open(`${API_BASE_URL}/passport-tuning/mapping/download`, "_blank")
  }

  const progress = total > 0 ? (mappedCount / total) * 100 : 0

  return (
    <TooltipProvider>
      <div className="min-h-screen bg-background w-full">
        <div className="w-full pt-6 pb-6 py-6">
          <div className="w-full px-4 md:px-6 lg:px-8 xl:px-12">
            <div className="mb-6">
              <h1 className="text-balance text-2xl font-semibold tracking-tight">Сопоставление кластеров заголовков</h1>
              <p className="text-pretty text-sm text-muted-foreground">
                Ручное сопоставление кластеров с ключами секций документов
              </p>
            </div>

            {/* Mobile Tabs */}
            <Tabs value={mobileTab} onValueChange={(v) => setMobileTab(v as typeof mobileTab)} className="lg:hidden mb-4">
              <TabsList className="grid w-full grid-cols-3">
                <TabsTrigger value="clusters">Кластеры</TabsTrigger>
                <TabsTrigger value="details">Детали</TabsTrigger>
                <TabsTrigger value="mapping">Маппинг</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          <div className="w-full min-h-[calc(100vh-200px)]">
            <div className="w-full px-4 md:px-6 lg:px-8 xl:px-12">
              <div className="w-full grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-[320px_minmax(0,1fr)_360px_420px] xl:grid-cols-[360px_minmax(0,1fr)_400px_460px] 2xl:grid-cols-[400px_minmax(0,1fr)_440px_500px] h-full">
              {/* Left Column: Cluster List */}
              <Card className={`rounded-xl shadow-sm flex flex-col min-h-0 min-w-0 ${mobileTab !== "clusters" ? "hidden" : ""} lg:flex md:col-span-1`}>
                <CardHeader className="pb-3 flex-shrink-0">
                  <CardTitle className="text-lg">Кластеры заголовков</CardTitle>
                  <div className="pt-3 space-y-3">
                    {/* Search */}
                    <div className="relative">
                      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        placeholder="Поиск по заголовкам…"
                        value={searchQuery}
                        onChange={(e) => {
                          setSearchQuery(e.target.value)
                          setPage(1)
                        }}
                        className="pl-9 h-9 text-sm"
                      />
                    </div>

                    {/* Filters */}
                    <Tabs value={filter} onValueChange={(v) => setFilter(v as typeof filter)}>
                      <TabsList className="grid w-full grid-cols-3 h-8">
                        <TabsTrigger value="all" className="text-xs">
                          Все ({total})
                        </TabsTrigger>
                        <TabsTrigger value="mapped" className="text-xs">
                          ✓ {mappedCount}
                        </TabsTrigger>
                        <TabsTrigger value="unmapped" className="text-xs">
                          ○ {unmappedCount}
                        </TabsTrigger>
                      </TabsList>
                    </Tabs>
                  </div>
                </CardHeader>
                <CardContent className="flex-1 min-h-0 min-w-0 flex flex-col px-4 pb-0">
                  <ScrollArea className="flex-1 pr-2 min-w-0">
                    {loading ? (
                      <div className="flex items-center justify-center py-12">
                        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                      </div>
                    ) : (
                      <div className="space-y-2 pb-2">
                        {paginatedClusters.map((cluster) => (
                          <button
                            key={cluster.cluster_id}
                            onClick={() => handleSelectCluster(cluster.cluster_id)}
                            className={`w-full rounded-lg border p-2.5 text-left transition-all hover:bg-accent ${
                              selectedClusterId === String(cluster.cluster_id)
                                ? "border-ring bg-accent ring-2 ring-ring ring-offset-1"
                                : "border-border"
                            }`}
                          >
                            <div className="flex items-start justify-between gap-2">
                              <div className="flex-1 space-y-1 min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="text-xs font-mono text-muted-foreground shrink-0">#{cluster.cluster_id}</span>
                                </div>
                                <p className="text-pretty text-sm leading-snug line-clamp-2">
                                  {cluster.top_titles_ru[0] || cluster.top_titles_en?.[0] || "Без заголовка"}
                                </p>
                              </div>
                              {(() => {
                                const mappingEntry = mapping[String(cluster.cluster_id)]
                                if (!mappingEntry) return null
                                
                                const mode = mappingEntry.mapping_mode || "single"
                                if (mode === "single") {
                                  return (
                                    <Badge
                                      variant="secondary"
                                      className="h-5 text-[10px] px-1.5 shrink-0 bg-green-100 text-green-800 hover:bg-green-100 border border-green-300 font-medium whitespace-nowrap"
                                      title="Однозначно"
                                    >
                                      ✓
                                    </Badge>
                                  )
                                } else if (mode === "ambiguous") {
                                  return (
                                    <Badge
                                      variant="secondary"
                                      className="h-5 text-[10px] px-1.5 shrink-0 bg-yellow-100 text-yellow-800 hover:bg-yellow-100 border border-yellow-300 font-medium whitespace-nowrap"
                                      title="Неоднозначно"
                                    >
                                      !
                                    </Badge>
                                  )
                                } else if (mode === "needs_split") {
                                  return (
                                    <Badge
                                      variant="secondary"
                                      className="h-5 text-[10px] px-1.5 shrink-0 bg-purple-100 text-purple-800 hover:bg-purple-100 border border-purple-300 font-medium whitespace-nowrap"
                                      title="Нужен сплит"
                                    >
                                      ⤴
                                    </Badge>
                                  )
                                } else if (mode === "skip") {
                                  return (
                                    <Badge
                                      variant="secondary"
                                      className="h-5 text-[10px] px-1.5 shrink-0 bg-gray-100 text-gray-800 hover:bg-gray-100 border border-gray-300 font-medium whitespace-nowrap"
                                      title="Пропущен"
                                    >
                                      ⦸
                                    </Badge>
                                  )
                                }
                                return null
                              })()}
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                  </ScrollArea>

                  {/* Pagination - Sticky */}
                  {!searchQuery && totalPages > 1 && (
                    <div className="sticky bottom-0 bg-background/80 backdrop-blur border-t pt-3 pb-3 mt-2 flex-shrink-0">
                      <div className="flex items-center justify-between gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setPage((p) => Math.max(1, p - 1))}
                          disabled={page === 1 || loading}
                          className="h-8 text-xs"
                        >
                          <ChevronLeft className="h-3 w-3 mr-1" />
                          Пред
                        </Button>
                        <span className="text-xs text-muted-foreground whitespace-nowrap">
                          {page} / {totalPages}
                        </span>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                          disabled={page === totalPages || loading}
                          className="h-8 text-xs"
                        >
                          След
                          <ChevronRight className="h-3 w-3 ml-1" />
                        </Button>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Center Column: Cluster Details */}
              <Card className={`rounded-xl shadow-sm flex flex-col min-h-0 min-w-0 ${mobileTab !== "details" ? "hidden" : ""} lg:flex md:col-span-1`}>
                <CardHeader className="flex-shrink-0 pb-3">
                  <CardTitle className="text-lg flex items-center gap-2">
                    <Target className="h-5 w-5" />
                    Детали кластера
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex-1 min-h-0 min-w-0">
                  {!selectedCluster ? (
                    <div className="flex h-full items-center justify-center">
                      <div className="text-center space-y-2">
                        <FileText className="h-12 w-12 mx-auto text-muted-foreground/50" />
                        <p className="text-sm text-muted-foreground">Выберите кластер из списка слева</p>
                      </div>
                    </div>
                  ) : (
                    <ScrollArea className="h-full pr-3 min-w-0">
                      <div className="space-y-4">
                        {/* RU Titles */}
                        <div className="space-y-2">
                          <button
                            onClick={() => setExpandedSections((s) => ({ ...s, ru: !s.ru }))}
                            className="flex items-center justify-between w-full text-sm font-medium hover:text-foreground transition-colors"
                          >
                            <span className="flex items-center gap-2">🇷🇺 Топ заголовки (RU)</span>
                            {expandedSections.ru ? (
                              <ChevronUp className="h-4 w-4" />
                            ) : (
                              <ChevronDown className="h-4 w-4" />
                            )}
                          </button>
                          {expandedSections.ru && (
                            <div className="rounded-lg border bg-muted/30 p-3">
                              <ul className="space-y-1.5">
                                {selectedCluster.top_titles_ru.slice(0, 20).map((title, i) => (
                                  <li key={i} className="text-sm leading-relaxed">
                                    • {title}
                                  </li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>

                        {/* EN Titles */}
                        {selectedCluster.top_titles_en && selectedCluster.top_titles_en.length > 0 && (
                          <div className="space-y-2">
                            <button
                              onClick={() => setExpandedSections((s) => ({ ...s, en: !s.en }))}
                              className="flex items-center justify-between w-full text-sm font-medium hover:text-foreground transition-colors"
                            >
                              <span className="flex items-center gap-2">🇬🇧 Топ заголовки (EN)</span>
                              {expandedSections.en ? (
                                <ChevronUp className="h-4 w-4" />
                              ) : (
                                <ChevronDown className="h-4 w-4" />
                              )}
                            </button>
                            {expandedSections.en && (
                              <div className="rounded-lg border bg-muted/30 p-3">
                                <ul className="space-y-1.5">
                                  {selectedCluster.top_titles_en.map((title, i) => (
                                    <li key={i} className="text-sm leading-relaxed">
                                      • {title}
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )}

                        {/* Examples */}
                        <div className="space-y-2">
                          <button
                            onClick={() => setExpandedSections((s) => ({ ...s, examples: !s.examples }))}
                            className="flex items-center justify-between w-full text-sm font-medium hover:text-foreground transition-colors"
                          >
                            <span className="flex items-center gap-2">📝 Примеры (до 10)</span>
                            {expandedSections.examples ? (
                              <ChevronUp className="h-4 w-4" />
                            ) : (
                              <ChevronDown className="h-4 w-4" />
                            )}
                          </button>
                          {expandedSections.examples && (
                            <div className="rounded-lg border bg-muted/30 p-3">
                              <div className="space-y-3">
                                {selectedCluster.examples.slice(0, 10).map((example, i) => (
                                  <div key={i} className="space-y-1">
                                    <code className="text-xs font-mono text-muted-foreground">
                                      {example.section_path}
                                    </code>
                                    <p className="text-sm leading-relaxed">{example.heading_text_raw}</p>
                                    {i < selectedCluster.examples.slice(0, 10).length - 1 && (
                                      <Separator className="mt-2" />
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>

                        {/* Statistics */}
                        {selectedCluster.stats && (
                          <div className="space-y-2">
                            <button
                              onClick={() => setExpandedSections((s) => ({ ...s, stats: !s.stats }))}
                              className="flex items-center justify-between w-full text-sm font-medium hover:text-foreground transition-colors"
                            >
                              <span className="flex items-center gap-2">📊 Статистика</span>
                              {expandedSections.stats ? (
                                <ChevronUp className="h-4 w-4" />
                              ) : (
                                <ChevronDown className="h-4 w-4" />
                              )}
                            </button>
                            {expandedSections.stats && (
                              <div className="rounded-lg border bg-muted/30 p-3 space-y-2">
                                {selectedCluster.stats.content_type_distribution && (
                                  <div>
                                    <p className="text-xs text-muted-foreground mb-1.5">Распределение типов:</p>
                                    <pre className="text-xs font-mono bg-muted p-2 rounded overflow-x-auto">
                                      {JSON.stringify(selectedCluster.stats.content_type_distribution, null, 2)}
                                    </pre>
                                  </div>
                                )}
                                {selectedCluster.stats.avg_total_chars !== undefined && (
                                  <div>
                                    <p className="text-xs text-muted-foreground">
                                      Средняя длина:{" "}
                                      <span className="font-medium text-foreground">
                                        {selectedCluster.stats.avg_total_chars.toFixed(2)} символов
                                      </span>
                                    </p>
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  )}
                </CardContent>
              </Card>

              {/* Middle Column: Context and Recommendations */}
              <Card className="rounded-xl shadow-sm flex flex-col min-h-0 min-w-0 hidden lg:flex">
                <CardHeader className="flex-shrink-0 pb-3">
                  <CardTitle className="text-lg">Контекст и рекомендации</CardTitle>
                </CardHeader>
                <CardContent className="flex-1 min-h-0 min-w-0 flex flex-col px-4 pb-0">
                  <ScrollArea className="flex-1 pr-2 min-w-0">
                    <div className="space-y-4 pb-2">
                      {/* Каноническая секция + Родитель */}
                      {sectionKey && taxonomyData && (() => {
                        const currentNode = taxonomyData.nodes.find((n) => n.section_key === sectionKey)
                        const alias = taxonomyData.aliases.find((a) => a.alias_key === sectionKey)
                        const canonicalKey = alias ? alias.canonical_key : sectionKey
                        const canonicalNode = taxonomyData.nodes.find((n) => n.section_key === canonicalKey)
                        const node = canonicalNode || currentNode
                        
                        if (!node) return null

                        const parent = node.parent_section_key
                          ? taxonomyData.nodes.find((n) => n.section_key === node.parent_section_key)
                          : null

                        return (
                          <div className="space-y-2">
                            <h3 className="text-sm font-medium">Каноническая секция</h3>
                            {/* Alias hint */}
                            {alias && (
                              <Alert className="border-blue-200 bg-blue-50 text-blue-900 dark:bg-blue-950/50 dark:text-blue-100">
                                <Info className="h-4 w-4" />
                                <AlertDescription className="text-xs">
                                  Алиас: <code className="font-mono">{alias.alias_key}</code> → будет сохранено как{" "}
                                  <code className="font-mono">{alias.canonical_key}</code>
                                </AlertDescription>
                              </Alert>
                            )}

                            {/* Canonical section */}
                            <div className="rounded-lg border bg-muted/30 p-2">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-xs font-medium">Каноническая секция:</span>
                                {node.is_narrow && (
                                  <Badge variant="secondary" className="h-4 text-[10px] px-1 bg-purple-100 text-purple-800">
                                    узкая
                                  </Badge>
                                )}
                              </div>
                              <code className="text-xs font-mono">{canonicalKey}</code>
                              <p className="text-xs text-muted-foreground mt-1">{node.title_ru}</p>
                            </div>

                            {/* Parent */}
                            {parent && (
                              <div className="rounded-lg border bg-muted/30 p-2">
                                <span className="text-xs font-medium">Родитель:</span>
                                <div className="mt-1">
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    className="h-7 text-xs"
                                    onClick={() => {
                                      setSectionKey(parent.section_key)
                                      const parentTitle = taxonomyData.nodes.find((n) => n.section_key === parent.section_key)?.title_ru
                                      if (parentTitle) setTitleRu(parentTitle)
                                    }}
                                  >
                                    {parent.section_key}
                                  </Button>
                                  <p className="text-xs text-muted-foreground">{parent.title_ru}</p>
                                </div>
                              </div>
                            )}

                            {/* Warning about narrow sections */}
                            {!node.is_narrow && (
                              (() => {
                                const narrowCandidates = [
                                  ...(selectedCluster?.candidate_section_1 ? [selectedCluster.candidate_section_1] : []),
                                  ...(selectedCluster?.candidate_section_2 ? [selectedCluster.candidate_section_2] : []),
                                  ...(selectedCluster?.candidate_section_3 ? [selectedCluster.candidate_section_3] : []),
                                ]
                                  .map((c) => taxonomyData.nodes.find((n) => n.section_key === c.section_key))
                                  .filter((n): n is TaxonomyNode => n !== undefined && n.is_narrow)

                                const narrowChildren = taxonomyData.nodes.filter(
                                  (n) => n.parent_section_key === canonicalKey && n.is_narrow
                                )

                                const related = taxonomyData.related
                                  .filter((r) => r.a_section_key === canonicalKey || r.b_section_key === canonicalKey)
                                  .map((r) => {
                                    const relatedKey = r.a_section_key === canonicalKey ? r.b_section_key : r.a_section_key
                                    return taxonomyData.nodes.find((n) => n.section_key === relatedKey)
                                  })
                                  .filter((n): n is TaxonomyNode => n !== undefined)

                                const narrowRelated = related.filter((r) => r.is_narrow)

                                const allNarrow = [...narrowCandidates, ...narrowChildren, ...narrowRelated]
                                const closeNarrow = allNarrow.filter((n) => {
                                  if (!selectedCluster) return false
                                  const candidate = [
                                    selectedCluster.candidate_section_1,
                                    selectedCluster.candidate_section_2,
                                    selectedCluster.candidate_section_3,
                                  ].find((c) => c?.section_key === n.section_key)
                                  if (!candidate) return false
                                  const topScore = selectedCluster.candidate_section_1?.score || 0
                                  return Math.abs(candidate.score - topScore) < 0.10
                                })

                                if (closeNarrow.length > 0) {
                                  return (
                                    <Alert className="border-yellow-200 bg-yellow-50 text-yellow-900 dark:bg-yellow-950/50 dark:text-yellow-100">
                                      <AlertCircle className="h-4 w-4" />
                                      <AlertDescription className="text-xs">
                                        Возможно лучше выбрать узкую секцию:{" "}
                                        {closeNarrow.map((n, i) => (
                                          <span key={n.section_key}>
                                            {i > 0 && ", "}
                                            <code className="font-mono">{n.section_key}</code>
                                          </span>
                                        ))}
                                      </AlertDescription>
                                    </Alert>
                                  )
                                }
                                return null
                              })()
                            )}
                          </div>
                        )
                      })()}

                        {/* Recommendations */}
                        {selectedCluster && (selectedCluster.candidate_section_1 ||
                          selectedCluster.candidate_section_2 ||
                          selectedCluster.candidate_section_3) && (
                          <div className="space-y-1.5 border-t pt-4">
                            <h3 className="text-sm font-medium">Рекомендуемые секции</h3>
                            <div className="space-y-1.5">
                              {[
                                selectedCluster.candidate_section_1,
                                selectedCluster.candidate_section_2,
                                selectedCluster.candidate_section_3,
                              ].map(
                                (candidate, idx) =>
                                  candidate && (
                                    <div
                                      key={idx}
                                      className="rounded-lg border border-muted bg-muted/20 p-2 space-y-1.5"
                                    >
                                      <div className="flex items-start justify-between gap-2">
                                        <div className="flex-1 space-y-1 min-w-0">
                                          <div className="flex items-center gap-2">
                                            <Badge variant="outline" className="h-5 text-xs shrink-0">
                                              {idx + 1}
                                            </Badge>
                                            <code className="text-xs font-mono break-all">{candidate.section_key}</code>
                                            {taxonomyData?.nodes.find((n) => n.section_key === candidate.section_key)?.is_narrow && (
                                              <Badge variant="secondary" className="h-4 text-[10px] px-1 bg-purple-100 text-purple-800">
                                                узкая
                                              </Badge>
                                            )}
                                          </div>
                                          <p className="text-xs leading-relaxed text-muted-foreground line-clamp-2">
                                            {candidate.title_ru}
                                          </p>
                                          <p className="text-xs text-muted-foreground">
                                            Score: {candidate.score.toFixed(2)}
                                          </p>
                                        </div>
                                      </div>
                                      <Button
                                        size="sm"
                                        variant="outline"
                                        onClick={() => handleSelectCandidate(candidate)}
                                        className="w-full h-8 text-xs"
                                      >
                                        Выбрать
                                      </Button>
                                    </div>
                                  ),
                              )}
                            </div>
                          </div>
                        )}

                      {/* Связанные секции */}
                      {sectionKey && taxonomyData && (() => {
                        const currentNode = taxonomyData.nodes.find((n) => n.section_key === sectionKey)
                        const alias = taxonomyData.aliases.find((a) => a.alias_key === sectionKey)
                        const canonicalKey = alias ? alias.canonical_key : sectionKey
                        const canonicalNode = taxonomyData.nodes.find((n) => n.section_key === canonicalKey)
                        const node = canonicalNode || currentNode
                        
                        if (!node) return null

                        const siblings = taxonomyData.nodes.filter(
                          (n) => n.parent_section_key === node.parent_section_key && n.section_key !== canonicalKey
                        )
                        const related = taxonomyData.related
                          .filter((r) => r.a_section_key === canonicalKey || r.b_section_key === canonicalKey)
                          .map((r) => {
                            const relatedKey = r.a_section_key === canonicalKey ? r.b_section_key : r.a_section_key
                            return taxonomyData.nodes.find((n) => n.section_key === relatedKey)
                          })
                          .filter((n): n is TaxonomyNode => n !== undefined)

                        if (siblings.length === 0 && related.length === 0) return null

                        return (
                          <div className="space-y-2 border-t pt-4">
                            <h3 className="text-sm font-medium">Связанные секции</h3>
                            
                            {/* Siblings */}
                            {siblings.length > 0 && (
                              <div className="rounded-lg border bg-muted/30 p-2">
                                <span className="text-xs font-medium">Соседние секции:</span>
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {siblings.map((sibling) => (
                                    <Button
                                      key={sibling.section_key}
                                      size="sm"
                                      variant="outline"
                                      className="h-7 text-xs"
                                      onClick={() => {
                                        setSectionKey(sibling.section_key)
                                        setTitleRu(sibling.title_ru)
                                      }}
                                    >
                                      {sibling.section_key.split(".").pop()}
                                    </Button>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Related sections */}
                            {related.length > 0 && (
                              <div className="rounded-lg border bg-muted/30 p-2">
                                <span className="text-xs font-medium">Связанные секции:</span>
                                <div className="mt-1 flex flex-wrap gap-1">
                                  {related.map((rel) => (
                                    <Button
                                      key={rel.section_key}
                                      size="sm"
                                      variant="outline"
                                      className="h-7 text-xs"
                                      onClick={() => {
                                        setSectionKey(rel.section_key)
                                        setTitleRu(rel.title_ru)
                                      }}
                                    >
                                      {rel.section_key.split(".").pop()}
                                    </Button>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        )
                      })()}

                        {!selectedCluster && (
                          <div className="flex h-full items-center justify-center py-8">
                            <div className="text-center space-y-2">
                              <FileText className="h-12 w-12 mx-auto text-muted-foreground/50" />
                              <p className="text-sm text-muted-foreground">Выберите кластер для просмотра контекста</p>
                            </div>
                          </div>
                        )}
                      </div>
                    </ScrollArea>
                  </CardContent>
                </Card>

              {/* Right Column: Mapping Configuration */}
              <Card className={`rounded-xl shadow-sm flex flex-col min-h-0 min-w-0 ${mobileTab !== "mapping" ? "hidden" : ""} lg:flex md:col-span-1 ${mappingMode === "ambiguous" ? "border-yellow-300 bg-yellow-50/30 dark:bg-yellow-950/10" : ""}`}>
                <CardHeader className="flex-shrink-0 pb-2">
                  <CardTitle className="text-base flex items-center gap-2">
                    ⚙️ Настройка маппинга
                    {mappingMode === "ambiguous" && (
                      <Badge variant="secondary" className="bg-yellow-100 text-yellow-800 border-yellow-200">
                        Неоднозначно
                      </Badge>
                    )}
                    {mappingMode === "needs_split" && (
                      <Badge variant="secondary" className="bg-purple-100 text-purple-800 border-purple-200">
                        Нужен сплит
                      </Badge>
                    )}
                  </CardTitle>
                </CardHeader>
                <CardContent className="flex-1 min-h-0 min-w-0 flex flex-col px-4 pb-0">
                  <ScrollArea className="flex-1 pr-2 min-w-0">
                    <div className="space-y-3 pb-2">
                      {/* Alerts */}
                      {alert.type && (
                        <Alert
                          variant={alert.type === "error" ? "destructive" : alert.type === "warning" ? "default" : "default"}
                          className={
                            alert.type === "success"
                              ? "border-green-500/50 bg-green-50 text-green-900 dark:bg-green-950/50 dark:text-green-100"
                              : alert.type === "warning"
                                ? "border-yellow-500/50 bg-yellow-50 text-yellow-900 dark:bg-yellow-950/50 dark:text-yellow-100"
                                : ""
                          }
                        >
                          {alert.type === "success" ? (
                            <CheckCircle2 className="h-4 w-4" />
                          ) : alert.type === "warning" ? (
                            <Info className="h-4 w-4" />
                          ) : (
                            <AlertCircle className="h-4 w-4" />
                          )}
                          <AlertDescription className="text-xs">{alert.message}</AlertDescription>
                        </Alert>
                      )}

                      {!selectedCluster ? (
                        <p className="text-sm text-muted-foreground text-center py-8">
                          Выберите кластер для настройки маппинга
                        </p>
                      ) : (
                        <>
                          {/* Form - перемещено в начало для лучшей видимости */}
                          <div className="space-y-2.5">
                            <div className="space-y-1.5">
                              <label className="text-sm font-medium">Режим маппинга</label>
                              <Select value={mappingMode} onValueChange={(v) => setMappingMode(v as MappingMode)}>
                                <SelectTrigger className="h-9">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="single">Однозначно</SelectItem>
                                  <SelectItem value="ambiguous">Неоднозначно</SelectItem>
                                  <SelectItem value="needs_split">Нужен сплит</SelectItem>
                                  <SelectItem value="skip">Пропустить</SelectItem>
                                </SelectContent>
                              </Select>
                              {mappingMode === "ambiguous" && (
                                <p className="text-xs text-yellow-600 dark:text-yellow-400">
                                  Не участвует в автотюнинге; попадёт в список на разбор
                                </p>
                              )}
                              {mappingMode === "needs_split" && (
                                <p className="text-xs text-purple-600 dark:text-purple-400">
                                  Рекомендуется разделить кластер; автотюнинг по умолчанию исключит
                                </p>
                              )}
                              {mappingMode === "skip" && (
                                <p className="text-xs text-muted-foreground">
                                  Кластер будет исключён из автотюнинга
                                </p>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <label className="text-sm font-medium">Тип документа</label>
                              <Select 
                                value={docType} 
                                onValueChange={(v) => setDocType(v as DocType)}
                              >
                                <SelectTrigger 
                                  className={`h-9 ${mappingMode === "ambiguous" ? "border-yellow-300 bg-yellow-50 dark:bg-yellow-950/20" : ""}`}
                                  disabled={mappingMode === "skip"}
                                >
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="protocol">Protocol</SelectItem>
                                  <SelectItem value="csr">CSR</SelectItem>
                                  <SelectItem value="sap">SAP</SelectItem>
                                  <SelectItem value="tfl">TFL</SelectItem>
                                  <SelectItem value="ib">IB</SelectItem>
                                  <SelectItem value="icf">ICF</SelectItem>
                                  <SelectItem value="other">Other</SelectItem>
                                </SelectContent>
                              </Select>
                            </div>

                            <div className="space-y-1.5">
                              <label className="text-sm font-medium">Section Key</label>
                              <Input
                                value={sectionKey}
                                onChange={(e) => setSectionKey(e.target.value)}
                                placeholder={`${docType}.section_name`}
                                list="section-keys"
                                className={`h-9 ${mappingMode === "ambiguous" ? "border-yellow-300 bg-yellow-50 dark:bg-yellow-950/20" : ""}`}
                                disabled={mappingMode === "skip"}
                              />
                              <datalist id="section-keys">
                                {sectionKeysByDocType[docType].map((key) => (
                                  <option key={key} value={key} />
                                ))}
                              </datalist>
                              {mappingMode !== "skip" && (
                                <p className="text-xs text-muted-foreground">
                                  Должен начинаться с <code className="font-mono">{docType}.</code>
                                </p>
                              )}
                            </div>

                            <div className="space-y-1.5">
                              <label className="text-sm font-medium">
                                Название (RU) <span className="text-muted-foreground font-normal">(опционально)</span>
                              </label>
                              <Input
                                value={titleRu}
                                onChange={(e) => setTitleRu(e.target.value)}
                                placeholder="Название секции на русском"
                                className={`h-9 ${mappingMode === "ambiguous" ? "border-yellow-300 bg-yellow-50 dark:bg-yellow-950/20" : ""}`}
                                disabled={mappingMode === "skip"}
                              />
                            </div>

                            <div className="space-y-1.5">
                              <label className="text-sm font-medium">
                                Комментарий <span className="text-muted-foreground font-normal">(опционально)</span>
                              </label>
                              <textarea
                                value={notes}
                                onChange={(e) => setNotes(e.target.value)}
                                placeholder="Короткая причина / комментарий"
                                rows={2}
                                maxLength={500}
                                className={`flex min-h-[50px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-none ${mappingMode === "ambiguous" ? "border-yellow-300 bg-yellow-50 dark:bg-yellow-950/20" : ""}`}
                              />
                              {(mappingMode === "ambiguous" || mappingMode === "needs_split" || mappingMode === "skip") && (
                                <p className="text-xs text-muted-foreground">
                                  Рекомендуется заполнить комментарий для проблемных кластеров
                                </p>
                              )}
                            </div>
                          </div>
                        </>
                      )}
                    </div>
                  </ScrollArea>

                  {/* Actions - Sticky */}
                  {selectedCluster && (
                    <div className="sticky bottom-0 bg-background/80 backdrop-blur border-t pt-3 pb-3 mt-2 flex-shrink-0 space-y-2">
                      <div className="space-y-2">
                        <Button onClick={handleSave} className="w-full" size="sm" disabled={saving}>
                          {saving ? (
                            <>
                              <Loader2 className="h-3 w-3 mr-2 animate-spin" />
                              Сохранение...
                            </>
                          ) : (
                            <>
                              <Save className="h-3 w-3 mr-2" />
                              Сохранить
                            </>
                          )}
                        </Button>
                        <Button
                          onClick={handleSaveAndNext}
                          variant="secondary"
                          className="w-full"
                          size="sm"
                          disabled={saving}
                        >
                          {saving ? (
                            <Loader2 className="h-3 w-3 mr-2 animate-spin" />
                          ) : (
                            <ArrowRight className="h-3 w-3 mr-2" />
                          )}
                          Сохранить и далее
                        </Button>
                        <Button onClick={handleClear} variant="ghost" className="w-full" size="sm" disabled={saving}>
                          <Trash2 className="h-3 w-3 mr-2" />
                          Очистить
                        </Button>
                      </div>

                      <Separator />

                      {/* Progress */}
                      <div className="space-y-2">
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-medium">Прогресс</span>
                          <span className="text-muted-foreground">
                            {mappedCount} / {total}
                          </span>
                        </div>
                        <Progress value={progress} className="h-1.5" />
                        <p className="text-xs text-muted-foreground">{progress.toFixed(1)}% размечено</p>
                      </div>

                      {/* Download */}
                      <Button onClick={handleDownload} variant="outline" className="w-full bg-transparent h-8 text-xs">
                        <Download className="h-3 w-3 mr-2" />
                        Скачать JSON
                      </Button>
                    </div>
                  )}
                </CardContent>
              </Card>
              </div>
            </div>
          </div>
        </div>
      </div>
    </TooltipProvider>
  )
}

export default function ClusterMappingPage() {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    }>
      <ClusterMappingContent />
    </Suspense>
  )
}
