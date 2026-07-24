import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PostProcessingRulesDialog from '@/components/business/PostProcessingRulesDialog.vue'
import type { PostProcessingRule } from '@/types/extraction'

const rules: PostProcessingRule[] = [
  {
    id: 'unit-standardization',
    key: 'unit_standardization',
    name: 'Unit Standardization',
    description: 'Format units to a consistent standard.',
    example: 'cm to cm',
    handler: 'builtin',
    enabled: true,
  },
  {
    id: 'space-removal',
    key: 'space_removal',
    name: 'Space Removal',
    description: 'Eliminate extra spaces.',
    example: 'A  B to A B',
    handler: 'builtin',
    enabled: false,
  },
]

function mountDialog() {
  return mount(PostProcessingRulesDialog, {
    props: {
      modelValue: true,
      rules,
    },
    global: {
      stubs: {
        Teleport: true,
        Transition: false,
      },
    },
  })
}

describe('Post-processing Rules 弹框', () => {
  it('规则开关只有点击 Confirm 后才通过 confirm 事件提交', async () => {
    const wrapper = mountDialog()

    await wrapper.find('.dialog-rule-toggle').trigger('click')
    expect(rules[0]?.enabled).toBe(true)
    expect(wrapper.emitted('confirm')).toBeUndefined()

    await wrapper.find('.confirm-rules-button').trigger('click')
    const confirmedRules = wrapper.emitted('confirm')?.[0]?.[0] as PostProcessingRule[]
    expect(confirmedRules[0]?.enabled).toBe(false)
  })

  it('新规则添加后默认启用并显示在列表最上方', async () => {
    const wrapper = mountDialog()
    const inputs = wrapper.findAll('.new-rule-card input')

    await inputs[0]?.setValue('Lowercase Normalization')
    await inputs[1]?.setValue('Normalize all Latin text to lowercase.')
    await inputs[2]?.setValue('Artifact A to artifact a')
    await wrapper.find('.new-rule-header > button').trigger('click')
    await wrapper.find('.confirm-rules-button').trigger('click')

    const confirmedRules = wrapper.emitted('confirm')?.[0]?.[0] as PostProcessingRule[]
    expect(confirmedRules[0]).toMatchObject({
      name: 'Lowercase Normalization',
      enabled: true,
      custom: true,
      handler: 'instruction',
    })
  })
})
