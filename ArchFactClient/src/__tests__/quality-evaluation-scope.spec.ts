import { describe, expect, it } from 'vitest'

import { filterEvaluationHistory, isCurrentEvaluationJob, reportKey } from '@/domain/quality-evaluation-scope'

describe('quality evaluation scope', () => {
  it('reuses a report baseline after another import with the same normalized filename', () => {
    expect(reportKey(' 文家山 .PDF')).toBe(reportKey('文家山.pdf'))
    expect(reportKey('新岗.pdf')).not.toBe(reportKey('文家山.pdf'))
  })

  it('keeps historical jobs read-only even when a baseline exists', () => {
    expect(isCurrentEvaluationJob('doc-new', 'doc-new')).toBe(true)
    expect(isCurrentEvaluationJob('doc-old', 'doc-new')).toBe(false)
  })

  it('filters history by report, search and evaluation status without discarding jobs', () => {
    const jobs = [
      { document_filename: '文家山.pdf', title: '基础研究', status: 'completed' },
      { document_filename: '文家山.pdf', title: '最新模板', status: 'pending' },
      { document_filename: '新岗.pdf', title: '基础研究', status: 'completed' },
    ]
    const options = {
      report: reportKey('文家山.pdf'), allReports: false, query: '基础', status: 'completed',
      statusOf: (job: typeof jobs[number]) => job.status,
      searchText: (job: typeof jobs[number]) => `${job.document_filename} ${job.title}`,
    }
    expect(filterEvaluationHistory(jobs, options)).toEqual([jobs[0]])
    expect(filterEvaluationHistory(jobs, { ...options, allReports: true })).toEqual([jobs[0], jobs[2]])
    expect(jobs).toHaveLength(3)
  })
})
