import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DataInsightsWorkspace from '@/components/business/DataInsightsWorkspace.vue'
import type { ExtractionRecord } from '@/types/extraction'

function record(id: string, category: string, page: number): ExtractionRecord {
  return {
    id,
    job_id: 'job-1',
    record_type: 'artifact',
    source_pages: [page],
    fields: {
      artifact_id: { raw_value: id, value: id, status: 'valid', evidence: [] },
      category: { raw_value: category, value: category, status: 'valid', evidence: [] },
    },
    warnings: [],
    review_status: 'unreviewed',
    reviewed_at: null,
    created_at: '',
  }
}

describe('data insights workspace', () => {
  it('filters cards by a chart selection and opens the matching source record', async () => {
    const wrapper = mount(DataInsightsWorkspace, {
      props: {
        active: false,
        jobId: 'job-1',
        records: [record('M1:1', '石钺', 12), record('M1:2', '玉琮', 13)],
        rawRecordCount: 4,
        requestedPages: [12, 13],
        documentName: 'report.pdf',
      },
    })

    expect(wrapper.findAll('.insights-record')).toHaveLength(2)
    await wrapper.findAll('.insights-bar').find((item) => item.text().includes('石钺'))!.trigger('click')
    expect(wrapper.findAll('.insights-record')).toHaveLength(1)
    expect(wrapper.find('.insights-record').text()).toContain('M1:1')
    await wrapper.find('.insights-evidence__heading button').trigger('click')
    expect(wrapper.emitted('open-record')?.[0]?.[0]).toMatchObject({ id: 'M1:1' })
  })

  it('shows a material chart when all artifact types are unclassified', async () => {
    const stone = record('M1:3', '石钺', 15)
    delete stone.fields.category
    stone.fields.material = { raw_value: '凝灰岩', value: '凝灰岩', status: 'valid', evidence: [] }
    const wrapper = mount(DataInsightsWorkspace, {
      props: {
        active: false,
        jobId: 'job-1',
        records: [stone],
        rawRecordCount: 2,
        requestedPages: [15],
        documentName: 'report.pdf',
      },
    })
    expect(wrapper.find('.insights-panel__heading h4').text()).toMatch(/材质|Material/)
    expect(wrapper.find('.insights-bar').text()).toContain('石质')
  })
})
