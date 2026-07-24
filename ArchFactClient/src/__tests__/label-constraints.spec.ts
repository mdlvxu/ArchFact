import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LabelConstraintSelector from '@/components/business/LabelConstraintSelector.vue'

function mountSelector(modelValue: 'Num' | 'Text' = 'Num') {
  return mount(LabelConstraintSelector, {
    props: {
      label: 'Artifact ID',
      modelValue,
      required: false,
      instruction: '',
    },
    global: {
      stubs: {
        Teleport: true,
      },
    },
  })
}

describe('Label constraint selector', () => {
  it('opens all supported type options and highlights the current value', async () => {
    const wrapper = mountSelector('Num')

    await wrapper.find('.constraint-trigger').trigger('click')

    expect(wrapper.findAll('.constraint-option').map((option) => option.text())).toEqual([
      'Num✓',
      'Text',
      'Date',
      'Yes/No',
      'Image',
      'Obj',
      'Arr',
    ])
    expect(wrapper.find('.constraint-option--active').text()).toContain('Num')
  })

  it('emits the selected type and closes the popup', async () => {
    const wrapper = mountSelector('Text')

    await wrapper.find('.constraint-trigger').trigger('click')
    await wrapper.findAll('.constraint-option')[2]?.trigger('click')

    expect(wrapper.emitted('update:modelValue')).toEqual([['Date']])
    expect(wrapper.find('.constraint-type-menu').exists()).toBe(false)
  })

  it('updates required and extraction instruction metadata', async () => {
    const wrapper = mountSelector('Text')

    await wrapper.find('.constraint-trigger').trigger('click')
    await wrapper.find('.constraint-required input').setValue(true)
    await wrapper.find('.constraint-instruction textarea').setValue('Extract from the figure caption.')

    expect(wrapper.emitted('update:required')).toEqual([[true]])
    expect(wrapper.emitted('update:instruction')).toEqual([['Extract from the figure caption.']])
  })
})
