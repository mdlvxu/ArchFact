import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ExtractionTemplateDialog from '@/components/business/ExtractionTemplateDialog.vue'
import { defaultExtractionTemplates } from '@/domain/extraction-defaults'
import type { ExtractionTemplate } from '@/types/extraction'

function mountDialog(selectedTemplateId = 'basic-research') {
  return mount(ExtractionTemplateDialog, {
    props: {
      modelValue: true,
      selectedTemplateId,
      templates: defaultExtractionTemplates,
    },
    global: {
      stubs: {
        Teleport: true,
        Transition: false,
      },
    },
  })
}

describe('Extraction Template 弹框', () => {
  it('确认时采用当前选中的内置模板', async () => {
    const wrapper = mountDialog('typology-research')

    await wrapper.find('.confirm-template-button').trigger('click')
    const template = wrapper.emitted('confirm')?.[0]?.[0] as ExtractionTemplate

    expect(template.id).toBe('typology-research')
    expect(template.fields.map((field) => field.label)).toContain('Subtype')
  })

  it('新建模板支持添加多个标签并在确认后返回完整数据', async () => {
    const wrapper = mountDialog()
    const inputs = wrapper.findAll('.new-template-card input')

    await inputs[0]?.setValue('Ceramic Analysis Template')
    await inputs[1]?.setValue('Ware Type')
    await wrapper.find('.label-input-row button').trigger('click')
    await inputs[1]?.setValue('Firing Technique')
    await wrapper.find('.label-input-row button').trigger('click')
    await wrapper.find('.confirm-template-button').trigger('click')

    const template = wrapper.emitted('confirm')?.[0]?.[0] as ExtractionTemplate
    expect(template.name).toBe('Ceramic Analysis Template')
    expect(template.fields.map((field) => field.label)).toEqual(['Ware Type', 'Firing Technique'])
    expect(template.fields.every((field) => field.key && field.type === 'Text')).toBe(true)
    expect(template.custom).toBe(true)
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([false])
  })
})
