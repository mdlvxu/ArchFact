export interface ProcessingMetricInput {
  startedAt: string | null
  endedAt: string | null
  processedPages: number
  totalPages: number
  progress?: number
  running: boolean
  nowMs: number
  locale?: 'zh-CN' | 'en-US'
}

export interface ProcessingMetricValues {
  startDate: string
  startTime: string
  elapsedTime: string
  timeLeft: string
  processingRate: string
}

function parseTimestamp(value: string | null) {
  if (!value) return null
  const trimmed = value.trim()
  // MongoDB returns UTC datetimes without an offset unless tz-aware decoding is enabled.
  // Browsers otherwise interpret that value as local time, which adds an eight-hour error in China.
  const normalized = /(?:z|[+-]\d{2}:?\d{2})$/i.test(trimmed) ? trimmed : `${trimmed}Z`
  const timestamp = Date.parse(normalized)
  return Number.isFinite(timestamp) ? timestamp : null
}

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function formatDuration(totalSeconds: number, locale: 'zh-CN' | 'en-US' = 'en-US') {
  const seconds = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainingSeconds = seconds % 60

  if (locale === 'zh-CN') {
    if (hours > 0) return `${hours}小时 ${minutes}分 ${remainingSeconds}秒`
    if (minutes > 0) return `${minutes}分 ${remainingSeconds}秒`
    return `${remainingSeconds}秒`
  }

  if (hours > 0) return `${hours}h ${minutes}m ${remainingSeconds}s`
  if (minutes > 0) return `${minutes}m ${remainingSeconds}s`
  return `${remainingSeconds}s`
}

function formatRate(pagesPerMinute: number, locale: 'zh-CN' | 'en-US' = 'en-US') {
  let value: string
  if (pagesPerMinute >= 10) value = String(Math.round(pagesPerMinute))
  else if (pagesPerMinute >= 1) value = pagesPerMinute.toFixed(1).replace(/\.0$/, '')
  else value = pagesPerMinute.toFixed(2)

  if (locale === 'zh-CN') return `${value} 页/分钟`
  return `${value} ${Math.abs(pagesPerMinute - 1) < 0.005 ? 'page' : 'pages'}/min`
}

function roundRemainingSeconds(seconds: number) {
  if (seconds <= 60) return Math.ceil(seconds / 5) * 5
  if (seconds <= 300) return Math.ceil(seconds / 15) * 15
  return Math.ceil(seconds / 60) * 60
}

/** 根据后端任务时间戳和实时页数生成 Processing Progress 展示指标。 */
export function buildProcessingMetrics(input: ProcessingMetricInput): ProcessingMetricValues {
  const locale = input.locale ?? 'en-US'
  const startedMs = parseTimestamp(input.startedAt)
  if (startedMs === null) {
    return {
      startDate: '—',
      startTime: '',
      elapsedTime: '—',
      timeLeft: '—',
      processingRate: '—',
    }
  }

  const startedDate = new Date(startedMs)
  const endedMs = input.running
    ? Math.max(startedMs, input.nowMs)
    : (parseTimestamp(input.endedAt) ?? startedMs)
  const elapsedMilliseconds = Math.max(0, endedMs - startedMs)
  const elapsedSeconds = elapsedMilliseconds / 1000
  const processedPages = Math.max(0, input.processedPages)
  const totalPages = Math.max(0, input.totalPages)
  const progressFraction = Number.isFinite(input.progress)
    ? Math.min(1, Math.max(0, Number(input.progress) / 100))
    : null
  const estimatedProcessedPages = totalPages > 0 && progressFraction !== null
    ? totalPages * progressFraction
    : processedPages
  const estimationReady = progressFraction === null
    ? processedPages > 0
    : progressFraction >= 0.08 && elapsedSeconds >= 3
  const pagesPerMinute = elapsedMilliseconds > 0 && estimatedProcessedPages > 0 && estimationReady
    ? estimatedProcessedPages / (elapsedMilliseconds / 60_000)
    : null
  const completed = progressFraction === 1 || (totalPages > 0 && processedPages >= totalPages)

  let timeLeft = '—'
  if (completed) {
    timeLeft = formatDuration(0, locale)
  } else if (input.running && progressFraction !== null && estimationReady) {
    const remainingSeconds = roundRemainingSeconds(
      elapsedSeconds * (1 - progressFraction) / progressFraction,
    )
    timeLeft = `~ ${formatDuration(remainingSeconds, locale)}`
  } else if (input.running && pagesPerMinute !== null && totalPages > processedPages) {
    const remainingSeconds = roundRemainingSeconds(
      (totalPages - processedPages) / pagesPerMinute * 60,
    )
    timeLeft = `~ ${formatDuration(remainingSeconds, locale)}`
  } else if (input.running) {
    timeLeft = locale === 'zh-CN' ? '计算中…' : 'Calculating…'
  }

  return {
    startDate: `${startedDate.getFullYear()}-${pad(startedDate.getMonth() + 1)}-${pad(startedDate.getDate())}`,
    startTime: `${pad(startedDate.getHours())}:${pad(startedDate.getMinutes())}:${pad(startedDate.getSeconds())}`,
    elapsedTime: formatDuration(elapsedSeconds, locale),
    timeLeft,
    processingRate: pagesPerMinute === null ? '—' : formatRate(pagesPerMinute, locale),
  }
}
