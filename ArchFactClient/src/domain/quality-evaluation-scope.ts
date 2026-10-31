/** A report-specific human baseline survives repeated PDF imports. */
export function reportKey(filename: string) {
  return filename.normalize('NFKC').replace(/\.pdf$/i, '').replace(/\s+/g, '').toLocaleLowerCase()
}

/** Only the currently opened document may start a new quality evaluation. */
export function isCurrentEvaluationJob(jobDocumentId: string | undefined, activeDocumentId: string) {
  return Boolean(jobDocumentId && activeDocumentId && jobDocumentId === activeDocumentId)
}

export function filterEvaluationHistory<T extends { document_filename: string }>(
  jobs: T[], options: {
    report: string
    allReports: boolean
    query: string
    status: string
    statusOf: (job: T) => string
    searchText: (job: T) => string
  },
) {
  const query = options.query.trim().toLocaleLowerCase()
  return jobs.filter((job) =>
    (options.allReports || reportKey(job.document_filename) === options.report) &&
    (options.status === 'all' || options.statusOf(job) === options.status) &&
    (!query || options.searchText(job).toLocaleLowerCase().includes(query)),
  )
}
