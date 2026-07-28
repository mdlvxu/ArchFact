import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import AssertionRules from '@/components/business/AssertionRules.vue'
import MachineVerificationWorkspace from '@/components/business/MachineVerificationWorkspace.vue'
import QualityEvaluationPanel from '@/components/business/QualityEvaluationPanel.vue'
import RematchReportDialog from '@/components/business/RematchReportDialog.vue'
import VersionHistory from '@/components/business/VersionHistory.vue'
import type { VerificationRule, VerificationVersion } from '@/types/verification'

const apiMocks = vi.hoisted(() => ({
  applyRematch: vi.fn(),
  cancelRematch: vi.fn(),
  createRematch: vi.fn(),
  createVerificationSession: vi.fn(),
  getRematch: vi.fn(),
  getRematchChanges: vi.fn(),
  createQualityEvaluation: vi.fn(),
  getQualityEvaluation: vi.fn(),
  getQualityEvaluations: vi.fn().mockResolvedValue([]),
  getGoldDatasets: vi.fn().mockResolvedValue([]),
  importWenjiashanGoldDataset: vi.fn(),
  getVerificationVersions: vi.fn(),
}))

vi.mock('@/api/modules/extraction', () => apiMocks)

vi.mock('element-plus', () => ({
  ElMessage: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  },
}))

const rules: VerificationRule[] = [
  { id: 1, title: 'First rule', description: 'First description', enabled: true },
  { id: 2, title: 'Second rule', description: 'Second description', enabled: true },
]

function createVersion(version: number): VerificationVersion {
  return {
    version,
    createdAt: `2026-07-15 10:0${version}`,
    title: `Version ${version}`,
    summary: `Summary ${version}`,
    matchingVersionId: `M${version}`,
    staleCount: 0,
    relationChangedCount: 0,
    exportable: true,
    before: 'Before',
    after: 'After',
    impact: {
      alignmentBefore: 40,
      alignmentAfter: 50,
      errorsBefore: 20,
      errorsAfter: 10,
      passedBefore: 80,
      passedAfter: 90,
    },
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('Machine Verification 页面组件', () => {
  it('通过 Add Rule 弹框保存，并将新规则插入列表最上方', async () => {
    const wrapper = mount(AssertionRules, {
      props: { rules, running: false },
      global: {
        stubs: { Teleport: true },
      },
    })

    await wrapper.find('.add-rule-button').trigger('click')
    expect(wrapper.find('.rule-dialog h2').text()).toBe('Add Rule')

    await wrapper.find('#verification-rule-name').setValue('Newest rule')
    await wrapper.find('#verification-rule-description').setValue('Newest description')
    await wrapper.find('.rule-dialog').trigger('submit')
    await nextTick()
    const emittedRules = wrapper.emitted('update:rules')?.[0]?.[0] as VerificationRule[]

    expect(emittedRules[0]?.id).toBe(3)
    expect(emittedRules[0]?.title).toBe('Newest rule')
    expect(emittedRules.slice(1)).toEqual(rules)
  })

  it('点击修改按钮时显示 Modify Rule，并只更新选中的规则', async () => {
    const wrapper = mount(AssertionRules, {
      props: { rules, running: false },
      global: {
        stubs: { Teleport: true },
      },
    })

    await wrapper.find('.rule-actions button').trigger('click')

    expect(wrapper.find('.rule-dialog h2').text()).toBe('Modify Rule')
    expect((wrapper.find('#verification-rule-name').element as HTMLInputElement).value).toBe('First rule')

    await wrapper.find('#verification-rule-name').setValue('Modified first rule')
    await wrapper.find('#verification-rule-description').setValue('Modified description')
    await wrapper.find('.rule-dialog').trigger('submit')
    await nextTick()
    const emittedRules = wrapper.emitted('update:rules')?.[0]?.[0] as VerificationRule[]

    expect(emittedRules[0]).toMatchObject({
      id: 1,
      title: 'Modified first rule',
      description: 'Modified description',
      updated: true,
    })
    expect(emittedRules[1]).toEqual(rules[1])
  })

  it('版本历史不依赖传入顺序，始终让最高版本置顶', () => {
    const wrapper = mount(VersionHistory, {
      props: { versions: [createVersion(1), createVersion(3), createVersion(2)] },
    })

    expect(wrapper.findAll('.version-badge')[0]?.text()).toBe('V3')
  })

  it('版本历史允许选择指定校验版本并显示其匹配版本', async () => {
    const wrapper = mount(VersionHistory, {
      props: {
        versions: [createVersion(2), createVersion(1)],
        selectedVersion: 1,
      },
    })

    expect(wrapper.find('.version-card--selected .version-badge').text()).toBe('V1')
    expect(wrapper.text()).toContain('M2')
    await wrapper.findAll('.version-card')[0]!.trigger('click')
    expect(wrapper.emitted('selectVersion')?.[0]).toEqual([2])
  })

  it('执行校验时创建后端会话并交给第二页处理固定样本', async () => {
    apiMocks.getVerificationVersions.mockResolvedValue([])
    apiMocks.createVerificationSession.mockResolvedValue({
      id: 'verify-1',
      job_id: 'job-1',
      cohort_id: 'cohort-1',
      target_version: 1,
      status: 'in_progress',
      rules,
      items: [],
      reviewed_count: 0,
      sample_count: 18,
      version_id: null,
      created_at: '2026-07-18T00:00:00Z',
      updated_at: '2026-07-18T00:00:00Z',
      completed_at: null,
    })
    const wrapper = mount(MachineVerificationWorkspace, {
      props: { jobId: 'job-1' },
    })
    await nextTick()
    await wrapper.find('.execute-button').trigger('click')
    await vi.waitFor(() => expect(apiMocks.createVerificationSession).toHaveBeenCalled())

    expect(wrapper.emitted('startVerification')?.[0]?.[0]).toMatchObject({
      id: 'verify-1',
      target_version: 1,
      sample_count: 18,
    })
  })

  it.skip('keeps rematch as a preview until the user explicitly applies it', async () => {
    // 第三页关系匹配面板已临时隐藏（showMatchingPanel=false）。
    apiMocks.getVerificationVersions.mockResolvedValue([])
    apiMocks.createRematch.mockResolvedValue({ rematch_id: 'rematch-1', status: 'queued' })
    const completedRun = {
      id: 'rematch-1',
      job_id: 'job-1',
      base_matching_version_id: 'M0',
      status: 'completed',
      preserve_reviewed: true,
      apply_immediately: false,
      cancel_requested: false,
      progress: { current: 1, total: 1, percent: 100, stage: 'completed' },
      report: {
        total_records: 18,
        linked_records: 16,
        partial_records: 1,
        unlinked_records: 1,
        complete_chains: 15,
        ocr_exact_relations: 20,
        layout_fallback_relations: 2,
        conflict_relations: 0,
        confidence: { high: 20, medium: 2, low: 1 },
        delta: { added: 3, removed: 1, changed: 2, unchanged: 17 },
        protection: {
          accepted_relations: 1,
          rejected_relations: 1,
          passed_records: 4,
          protected_relations: 6,
        },
      },
      error: null,
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:01Z',
      completed_at: '2026-07-19T00:00:01Z',
      applied_at: null,
    }
    apiMocks.getRematch.mockResolvedValue(completedRun)
    apiMocks.applyRematch.mockResolvedValue({
      ...completedRun,
      status: 'applied',
      applied_at: '2026-07-19T00:00:02Z',
    })
    const wrapper = mount(MachineVerificationWorkspace, {
      props: { jobId: 'job-1', activeMatchingVersionId: 'M0' },
    })
    await nextTick()

    await wrapper.find('.matching-panel__actions button').trigger('click')
    await vi.waitFor(() => expect(apiMocks.createRematch).toHaveBeenCalledWith('job-1'))
    expect(wrapper.emitted('matchingVersionApplied')).toBeUndefined()

    await vi.waitFor(() => expect(wrapper.find('.matching-button--apply').exists()).toBe(true))
    await wrapper.find('.matching-button--apply').trigger('click')
    await vi.waitFor(() => {
      expect(wrapper.emitted('matchingVersionApplied')?.[0]).toEqual(['rematch-1'])
    })
  })

  it('shows the latest gold-standard quality baseline by metric family', async () => {
    apiMocks.getGoldDatasets.mockResolvedValue([
      {
        id: 'gold-1',
        name: 'Wenjiashan',
        document_id: 'doc-1',
        version: '1.0',
        status: 'ready',
        source_type: 'human_annotation',
        record_count: 400,
        region_count: 0,
        asset_count: 0,
        link_count: 0,
        matched_artifact_assets: 0,
        matched_color_plate_assets: 0,
        source_document_verified: true,
        warnings: [],
        created_at: '2026-07-22T00:00:00Z',
        updated_at: '2026-07-22T00:00:00Z',
      },
    ])
    apiMocks.getQualityEvaluations.mockResolvedValue([
      {
        id: 'quality-1',
        job_id: 'job-1',
        document_id: 'doc-1',
        dataset_id: 'gold-1',
        dataset_version: '1.0',
        matching_version_id: 'M0',
        status: 'completed',
        progress: { current: 5, total: 5, percent: 100, stage: 'completed' },
        summary: {
          predicted_records: 20,
          gold_records: 400,
          matched_records: 18,
          unmatched_predicted_records: 2,
          ambiguous_records: 0,
          artifact_id_precision: 0.9,
          artifact_id_recall: null,
          field_macro_score: 0.86,
          ocr_anchor_score: 0.92,
          relation_score: 0.88,
          detection_macro_f1: 0.8,
          full_document_scope: false,
          evaluated_pages: [19, 20],
        },
        field_metrics: [],
        ocr_metrics: [],
        detection_metrics: [],
        relation_metrics: {},
        unmatched: {},
        warnings: [],
        error: null,
        created_at: '2026-07-22T00:00:00Z',
        updated_at: '2026-07-22T00:00:01Z',
        completed_at: '2026-07-22T00:00:01Z',
      },
    ])
    const wrapper = mount(QualityEvaluationPanel, {
      props: { jobId: 'job-1', documentId: 'doc-1' },
    })

    await vi.waitFor(() => expect(apiMocks.getQualityEvaluations).toHaveBeenCalledWith('job-1'))
    await vi.waitFor(() => expect(wrapper.findAll('.quality-score-grid article')).toHaveLength(5))
    expect(wrapper.text()).toContain('90%')
    expect(wrapper.text()).toContain('92%')
  })

  it('重匹配报告可按新增、移除和修改关系筛选', async () => {
    const run = {
      id: 'M1',
      job_id: 'job-1',
      base_matching_version_id: 'M0',
      status: 'completed' as const,
      preserve_reviewed: true,
      apply_immediately: false,
      cancel_requested: false,
      progress: { current: 1, total: 1, percent: 100, stage: 'completed' },
      report: {
        total_records: 2,
        linked_records: 2,
        partial_records: 0,
        unlinked_records: 0,
        complete_chains: 2,
        ocr_exact_relations: 1,
        layout_fallback_relations: 1,
        conflict_relations: 0,
        confidence: { high: 1, medium: 1, low: 0 },
        delta: { added: 1, removed: 0, changed: 1, unchanged: 0 },
        protection: {
          accepted_relations: 0,
          rejected_relations: 0,
          passed_records: 0,
          protected_relations: 0,
        },
      },
      error: null,
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:01Z',
      completed_at: '2026-07-19T00:00:01Z',
      applied_at: null,
    }
    const wrapper = mount(RematchReportDialog, {
      props: {
        run,
        changes: [
          {
            change: 'added',
            relation_id: 'rel-added',
            relation_type: 'number_to_artifact',
            source_region_id: 'n-1',
            target_region_id: 'a-1',
            before_method: null,
            after_method: 'ocr_exact',
            before_score: null,
            after_score: 0.94,
            protected: false,
          },
          {
            change: 'changed',
            relation_id: 'rel-changed',
            relation_type: 'caption_to_number',
            source_region_id: 'c-1',
            target_region_id: 'n-1',
            before_method: 'nearest',
            after_method: 'ocr_exact',
            before_score: 0.6,
            after_score: 0.92,
            protected: true,
          },
        ],
      },
    })

    expect(wrapper.findAll('.relation-change-list article')).toHaveLength(2)
    await wrapper.findAll('.relation-changes nav button')[1]!.trigger('click')
    expect(wrapper.findAll('.relation-change-list article')).toHaveLength(1)
    expect(wrapper.text()).toContain('number_to_artifact')
  })
})
