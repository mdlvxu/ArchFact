import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LabelConstraintSelector from '@/components/business/LabelConstraintSelector.vue'

function mountSelector(instruction = 'Extract from the source text.') {
  return mount(LabelConstraintSelector, {
    props: {
      label: 'Artifact ID',
      required: false,
      instruction,
      defaultInstruction: 'Extract from the source text.',
    },
    global: { stubs: { Teleport: true } },
  })
}

describe('Label constraint selector', () => {
  it('opens a text-only prompt editor without type controls', async () => {
    const wrapper = mountSelector()
    await wrapper.find('.constraint-trigger').trigger('click')

    expect(wrapper.find('.constraint-type-menu').exists()).toBe(true)
    expect(wrapper.find('.constraint-instruction textarea').exists()).toBe(true)
    expect(wrapper.find('.constraint-option').exists()).toBe(false)
  })

  it('saves the edited prompt and closes the editor', async () => {
    const wrapper = mountSelector()
    await wrapper.find('.constraint-trigger').trigger('click')
    await wrapper.find('.constraint-instruction textarea').setValue('Extract the exact artifact ID.')
    await wrapper.find('.constraint-details__save').trigger('click')

    expect(wrapper.emitted('update:instruction')).toEqual([['Extract the exact artifact ID.']])
    expect(wrapper.find('.constraint-type-menu').exists()).toBe(false)
  })
})
