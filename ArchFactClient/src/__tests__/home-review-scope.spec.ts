import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import * as api from '@/api/modules/extraction'
import ArchaeologicalCatalogs from '@/components/business/ArchaeologicalCatalogs.vue'
import HomeView from '@/views/HomeView.vue'
import type { ExtractionJob, ExtractionRecord } from '@/types/extraction'
import type { VerificationSession } from '@/types/verification'

vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: {}, getDocument: vi.fn() }))
vi.mock('element-plus/es/components/button/style/css', () => ({}))
vi.mock('element-plus/es/components/dropdown/style/css', () => ({}))
vi.mock('element-plus/es/components/dropdown-item/style/css', () => ({}))
vi.mock('element-plus/es/components/dropdown-menu/style/css', () => ({}))
vi.mock('element-plus/es/components/icon/style/css', () => ({}))
vi.mock('element-plus/es/components/message/style/css', () => ({}))
vi.mock('element-plus/es/components/base/style/css', () => ({}))
vi.mock('@/components/business/MachineVerificationWorkspace.vue', () => ({
  name: 'MachineVerificationWorkspace',
  __isTeleport: false,
  default: { name: 'MachineVerificationWorkspace', emits: ['start-verification'], render: () => null },
}))
vi.mock('@/components/business/DataInsightsWorkspace.vue', () => ({
  name: 'DataInsightsWorkspace',
  __isTeleport: false,
  default: { name: 'DataInsightsWorkspace', props: ['records'], render: () => null },
}))
vi.mock('@/components/business/ProcessingPanel.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/business/ExtractionSettings.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/business/ContentPreview.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/business/PageNavigator.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/business/PdfOverview.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/business/RelatedPages.vue', () => ({ default: { render: () => null } }))
vi.mock('@/components/business/LanguageToggle.vue', () => ({ default: { render: () => null } }))
vi.mock('@/api/modules/extraction', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/api/modules/extraction')>(),
  getLatestCompletedExtractionJob: vi.fn(),
  getExtractionJob: vi.fn(),
  getArtifactEntityThumbnails: vi.fn(),
  getExtractionRecords: vi.fn(),
  getUploadedDocument: vi.fn(),
  getDocumentImages: vi.fn(),
  getVerificationSessionRecords: vi.fn(),
  getRecordEvidenceContext: vi.fn(),
  updateVerificationSessionItem: vi.fn(),
}))

function record(index: number): ExtractionRecord {
  return {
    id: `record-${index}`, job_id: 'job-1', record_type: 'artifact',
    source_pages: [1], entity_id: `entity-${index}`,
    primary_artifact_region_id: `crop-${index}`,
    fields: { category: { value: '陶罐', raw_value: '陶罐', status: 'valid', evidence: [] } },
    warnings: [], review_status: 'unreviewed', reviewed_at: null, created_at: '',
  }
}

function session(ids: string[], id = 'session-1'): VerificationSession {
  return {
    id, job_id: 'job-1', cohort_id: 'cohort-1', target_version: 1,
    status: 'in_progress', rules: [],
    items: ids.map((record_id) => ({ record_id, verdict: 'unreviewed', failure_reason: '', reviewed_at: null })),
    sample_count: ids.length, reviewed_count: 0, version_id: null,
    created_at: '', updated_at: '', completed_at: null,
  }
}

const job: ExtractionJob = {
  id: 'job-1', document_id: 'document-1', pipeline_id: 'test', status: 'completed',
  stage: 'completed', progress: { current: 1, total: 1, percent: 100 },
  cancel_requested: false, error: null, page_issues: [], succeeded_pages: 1,
  failed_pages: 0, requested_pages: [1], events: [], created_at: '', updated_at: '',
}

const wrappers: ReturnType<typeof shallowMount>[] = []
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  vi.mocked(api.getLatestCompletedExtractionJob).mockResolvedValue(job)
  vi.mocked(api.getExtractionJob).mockResolvedValue(job)
  vi.mocked(api.getArtifactEntityThumbnails).mockResolvedValue({})
  vi.mocked(api.getDocumentImages).mockResolvedValue([])
  vi.mocked(api.getUploadedDocument).mockResolvedValue({
    document_id: 'document-1', filename: 'report.pdf', page_count: 1,
  } as Awaited<ReturnType<typeof api.getUploadedDocument>>)
  vi.mocked(api.getRecordEvidenceContext).mockImplementation(async (_job, id) => {
    const current = record(Number(id.replace('record-', '')))
    return { record: current, text_record: current, primary_text_page: 0, page_numbers: [], regions: [], relations: [] }
  })
})
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  localStorage.clear()
})

async function openHome(records: ExtractionRecord[]) {
  vi.mocked(api.getExtractionRecords).mockResolvedValue(records)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: HomeView },
      { path: '/preview', component: HomeView },
      { path: '/machine-verification', component: HomeView },
      { path: '/gt-evaluation', component: HomeView },
      { path: '/insights', component: HomeView },
    ],
  })
  await router.push('/machine-verification')
  await router.isReady()
  const wrapper = shallowMount(HomeView, {
    global: {
      plugins: [router],
      stubs: { AppShell: false, ArchaeologicalCatalogs: false, KeepAlive: false },
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

describe('Home human review sample scope', () => {
  it('uses the entity crop for a text representative when the drawing lives on another page', async () => {
    const drawing = {
      ...record(1),
      id: 'drawing-record',
      entity_id: 'entity-t33-1',
      thumbnail_region_id: null,
      primary_artifact_region_id: null,
      fields: { artifact_id: { value: 'T33:1', raw_value: 'T33:1', status: 'valid', evidence: [] } },
    } as ExtractionRecord
    const text = {
      ...record(2),
      id: 'text-record',
      entity_id: 'entity-t33-1',
      thumbnail_region_id: null,
      primary_artifact_region_id: null,
      fields: {
        artifact_id: { value: 'T33:1', raw_value: 'T33:1', status: 'valid', evidence: [] },
        morphological_description: {
          value: '鼓腹，平底。', raw_value: '鼓腹，平底。', status: 'valid', evidence: [],
        },
      },
    } as ExtractionRecord
    vi.mocked(api.getArtifactEntityThumbnails).mockResolvedValue({
      'entity-t33-1': 'crop-t33-1',
    })

    const { wrapper } = await openHome([drawing, text])
    const catalog = wrapper.findComponent(ArchaeologicalCatalogs)
    await flushPromises()

    expect(catalog.props('records')).toHaveLength(1)
    expect(catalog.props('records')[0].id).toBe('text-record')
    expect(catalog.props('records')[0].thumbnail_region_id).toBe('crop-t33-1')
  })

  it('shows only the 18 session samples, keeping missing crops and shared entities, while insights retain all cards', async () => {
    const records = Array.from({ length: 40 }, (_, i) => record(i))
    const { wrapper } = await openHome(records)
    const catalog = wrapper.findComponent(ArchaeologicalCatalogs)
    expect(catalog.props('records')).toHaveLength(40)

    const samples = records.slice(3, 21).map((item) => ({ ...item }))
    samples[0]!.primary_artifact_region_id = null
    samples[1]!.entity_id = samples[2]!.entity_id
    const fixedSession = session(samples.map((item) => item.id))
    vi.mocked(api.getRecordEvidenceContext).mockImplementation(async (_job, id) => {
      const current = samples.find((item) => item.id === id)!
      return { record: current, text_record: current, primary_text_page: 0, page_numbers: [], regions: [], relations: [] }
    })
    // Even a too-broad endpoint response must not broaden the fixed review cohort.
    const sampleIds = new Set(samples.map((item) => item.id))
    vi.mocked(api.getVerificationSessionRecords).mockResolvedValue([
      ...records.filter((item) => !sampleIds.has(item.id)), ...samples,
    ].reverse())
    wrapper.findComponent({ name: 'MachineVerificationWorkspace' }).vm.$emit('start-verification', fixedSession)
    await flushPromises()

    expect(catalog.props('mode')).toBe('verify')
    expect(catalog.props('records').map((item: ExtractionRecord) => item.id)).toEqual(fixedSession.items.map((item) => item.record_id))
    expect(catalog.findAll('.catalog-item')).toHaveLength(18)
    expect(catalog.props('records')[0].primary_artifact_region_id).toBeNull()
    expect(api.getVerificationSessionRecords).toHaveBeenCalledWith('job-1', fixedSession.id)

    catalog.vm.$emit('review', records[0], 'passed')
    await flushPromises()
    expect(api.updateVerificationSessionItem).not.toHaveBeenCalled()

    vi.mocked(api.updateVerificationSessionItem).mockResolvedValue({
      ...fixedSession, reviewed_count: 1,
      items: fixedSession.items.map((item, i) => i === 0 ? { ...item, verdict: 'passed' } : item),
    })
    catalog.vm.$emit('review', samples[0], 'passed')
    await flushPromises()
    expect(api.updateVerificationSessionItem).toHaveBeenCalledWith('job-1', fixedSession.id, samples[0]!.id, 'passed', undefined, '')
    expect(catalog.findAll('.catalog-item')).toHaveLength(18)
  })

  it('clears old samples while a different review session loads, without falling back to all records', async () => {
    const records = Array.from({ length: 40 }, (_, i) => record(i))
    const { wrapper } = await openHome(records)
    const catalog = wrapper.findComponent(ArchaeologicalCatalogs)
    const workspace = wrapper.findComponent({ name: 'MachineVerificationWorkspace' })
    vi.mocked(api.getVerificationSessionRecords).mockResolvedValue(records.slice(0, 18))
    workspace.vm.$emit('start-verification', session(records.slice(0, 18).map((item) => item.id)))
    await flushPromises()
    expect(catalog.findAll('.catalog-item')).toHaveLength(18)

    let finish!: (value: ExtractionRecord[]) => void
    vi.mocked(api.getVerificationSessionRecords).mockReturnValue(new Promise((resolve) => { finish = resolve }))
    workspace.vm.$emit('start-verification', session(records.slice(18, 36).map((item) => item.id), 'session-2'))
    await flushPromises()
    expect(catalog.props('records')).toHaveLength(0)
    finish(records.slice(18, 36))
    await flushPromises()
    expect(catalog.props('records').map((item: ExtractionRecord) => item.id)).toEqual(records.slice(18, 36).map((item) => item.id))
  })
})
