import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import PageRangeDialog from '@/components/business/PageRangeDialog.vue'
import { getDefaultExtractionPages } from '@/domain/page-selection'
import type { PdfPageItem } from '@/types/pdf'

function createPages(total: number): PdfPageItem[] {
  return Array.from({ length: total }, (_, index) => ({
    page: index + 1,
    thumbnailUrl: `page-${index + 1}.jpg`,
    loading: false,
  }))
}

function mountDialog(selectedPages = [1, 2, 3, 4, 5]) {
  return mount(PageRangeDialog, {
    props: {
      modelValue: true,
      pages: createPages(20),
      selectedPages,
    },
    global: {
      stubs: {
        Teleport: true,
        Transition: false,
      },
    },
  })
}

describe('Page Range 弹框', () => {
  it('PDF 导入后默认选择前五页，文档不足五页时选择全部已有页', () => {
    expect(getDefaultExtractionPages(createPages(20))).toEqual([1, 2, 3, 4, 5])
    expect(getDefaultExtractionPages(createPages(3))).toEqual([1, 2, 3])
  })

  it('支持组合页码范围并按升序去重提交', async () => {
    const wrapper = mountDialog()
    const input = wrapper.find('.range-input-section input')

    await input.setValue('1-3, 8, 10-12, 3')
    await wrapper.find('.range-input-section button').trigger('click')
    await wrapper.find('.confirm-pages-button').trigger('click')

    expect(wrapper.emitted('confirm')?.[0]?.[0]).toEqual([1, 2, 3, 8, 10, 11, 12])
  })

  it('支持奇数页选择和反选，最终得到全部偶数页', async () => {
    const wrapper = mountDialog([])
    const paritySelect = wrapper.find('.quick-action-grid select')

    await paritySelect.setValue('odd')
    expect(wrapper.findAll('.page-selection-item--selected')).toHaveLength(10)
    await wrapper.findAll('.quick-action-grid button')[2]?.trigger('click')
    await wrapper.find('.confirm-pages-button').trigger('click')

    expect(wrapper.emitted('confirm')?.[0]?.[0]).toEqual([2, 4, 6, 8, 10, 12, 14, 16, 18, 20])
  })

  it('拒绝超出 PDF 总页数的范围', async () => {
    const wrapper = mountDialog()

    await wrapper.find('.range-input-section input').setValue('18-24')
    await wrapper.find('.confirm-pages-button').trigger('click')

    expect(wrapper.emitted('confirm')).toBeUndefined()
    expect(wrapper.find('.range-input-section [role="alert"]').text()).toContain('outside pages 1-20')
  })

  it('Apply 后继续手动选页不会在 Confirm 时被输入框覆盖', async () => {
    const wrapper = mountDialog([])
    await wrapper.find('.range-input-section input').setValue('1-3')
    await wrapper.find('.range-input-section button').trigger('click')
    await wrapper.findAll('.page-selection-item')[4]?.trigger('click')
    await wrapper.find('.confirm-pages-button').trigger('click')

    expect(wrapper.emitted('confirm')?.[0]?.[0]).toEqual([1, 2, 3, 5])
  })

  it('长文档只渲染可视窗口附近的页面组件', () => {
    const wrapper = mount(PageRangeDialog, {
      props: {
        modelValue: true,
        pages: createPages(356),
        selectedPages: [],
      },
      global: { stubs: { Teleport: true, Transition: false } },
    })

    expect(wrapper.findAll('.page-selection-item').length).toBeLessThan(60)
  })
})
