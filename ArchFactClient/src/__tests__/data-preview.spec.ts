import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import ContentPreview from '@/components/business/ContentPreview.vue'
import ExtractionDetails from '@/components/business/ExtractionDetails.vue'
import RelatedPages from '@/components/business/RelatedPages.vue'
import type { PreviewAnnotation } from '@/types/extraction'
import type { PdfPageItem } from '@/types/pdf'

function createPages(total: number): PdfPageItem[] {
  return Array.from({ length: total }, (_, index) => ({
    page: index + 1,
    thumbnailUrl: '',
    loading: false,
  }))
}

describe('Data Preview 页面组件', () => {
  it('使用缓动动画整体缩放 PDF 内容', async () => {
    const wrapper = mount(ContentPreview, {
      props: {
        page: 7,
        previewUrl: 'data:image/jpeg;base64,preview',
        fileName: 'catalog.pdf',
        loading: false,
      },
    })
    const content = wrapper.find('.pdf-page-frame').element as HTMLElement
    const animation = {
      onfinish: null,
      finish: vi.fn(),
      cancel: vi.fn(),
    } as unknown as Animation
    const animate = vi.fn(() => animation)
    Object.defineProperty(content, 'animate', { configurable: true, value: animate })

    await wrapper.find('.zoom-controls button:last-child').trigger('click')
    await nextTick()

    expect(animate).toHaveBeenCalledWith(
      [
        { transform: 'scale3d(0.8, 0.8, 1)' },
        { transform: 'scale3d(1, 1, 1)' },
      ],
      {
        duration: 260,
        easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
        fill: 'both',
      },
    )
  })

  it('放大 PDF 后将横向和纵向滚动位置重新定位到中心', async () => {
    const wrapper = mount(ContentPreview, {
      props: {
        page: 7,
        previewUrl: 'data:image/jpeg;base64,preview',
        fileName: 'catalog.pdf',
        loading: false,
      },
    })
    const stage = wrapper.find('.pdf-page-stage').element as HTMLElement
    const scrollTo = vi.fn()

    Object.defineProperties(stage, {
      scrollWidth: { configurable: true, value: 1200 },
      clientWidth: { configurable: true, value: 800 },
      scrollHeight: { configurable: true, value: 900 },
      clientHeight: { configurable: true, value: 600 },
      scrollTo: { configurable: true, value: scrollTo },
    })

    await wrapper.find('.zoom-controls button:last-child').trigger('click')
    await nextTick()

    expect(wrapper.find('.zoom-controls__value').text()).toBe('125%')
    expect(scrollTo).toHaveBeenLastCalledWith({
      left: 200,
      top: 150,
      behavior: 'smooth',
    })
  })

  it('当前 PDF 页变化时同步刷新提取记录编号', async () => {
    const wrapper = mount(ExtractionDetails, {
      props: {
        page: 12,
        total: 30,
        fileName: 'catalog.pdf',
      },
    })

    expect(wrapper.text()).toContain('AF-012-01')
    await wrapper.setProps({ page: 13 })
    expect(wrapper.text()).toContain('AF-013-01')
  })

  it('相关页面点击后向父组件发送目标页码', async () => {
    const evidence: PreviewAnnotation[] = [
      {
        id: 'line-62',
        regionId: 'region-line-62',
        page: 62,
        kind: 'line_drawing',
        label: 'Line drawing',
        quote: 'H125:1',
        bbox: [0.2, 0.2, 0.4, 0.5],
        approximate: false,
        cropUrl: '/api/v1/extraction-jobs/job-1/regions/region-line-62/crop',
      },
      {
        id: 'text-19',
        regionId: 'region-text-19',
        page: 19,
        kind: 'text',
        label: 'Text evidence',
        quote: 'H125:1 fine-paste gray pottery',
        bbox: [0.1, 0.7, 0.8, 0.8],
        approximate: false,
      },
    ]
    const pages = createPages(80)
    pages[61]!.thumbnailUrl = 'data:image/png;base64,page-62'
    const wrapper = mount(RelatedPages, {
      props: {
        pages,
        activePage: 19,
        annotations: evidence,
      },
    })

    const pageButtons = wrapper.findAll('.related-item')
    expect(pageButtons).toHaveLength(4)
    expect(pageButtons[2]?.attributes('disabled')).toBeUndefined()
    expect(pageButtons[3]?.attributes('disabled')).toBeDefined()
    expect(pageButtons[3]?.text()).toContain('No linked color plate')
    expect(pageButtons[3]?.text()).toContain('4/4')
    expect(pageButtons[0]?.find('img').attributes('src'))
      .toBe('data:image/png;base64,page-62')
    expect(pageButtons[2]?.find('img').attributes('src'))
      .toBe('/api/v1/extraction-jobs/job-1/regions/region-line-62/crop')
    await pageButtons[0]?.trigger('click')
    expect(wrapper.emitted('selectAnnotation')?.[0]).toEqual(['line-62'])

    const colorAnnotation: PreviewAnnotation = {
      ...evidence[0]!,
      id: 'color-70',
      regionId: 'region-color-70',
      page: 70,
      kind: 'color_plate',
      label: 'Color Plate',
      cropUrl: '/api/v1/extraction-jobs/job-1/regions/region-color-70/crop',
    }
    pages[69]!.thumbnailUrl = 'data:image/png;base64,page-70'
    await wrapper.setProps({ pages, annotations: [...evidence, colorAnnotation] })
    const colorCard = wrapper.findAll('.related-item')[3]!
    expect(colorCard.attributes('disabled')).toBeUndefined()
    expect(colorCard.find('img').attributes('src')).toBe('data:image/png;base64,page-70')
    expect(colorCard.text()).toContain('Color Plate')
    expect(colorCard.text()).toContain('4/4')
  })

  it('抽取页不连续时按列表位置显示当前页附近的相关页面', () => {
    const pages = [100, 220, 365, 480].map((page) => ({
      page,
      thumbnailUrl: '',
      loading: false,
    }))
    const wrapper = mount(RelatedPages, {
      props: {
        pages,
        activePage: 220,
      },
    })

    expect(wrapper.findAll('.related-item')).toHaveLength(0)
    expect(wrapper.find('.related-empty').exists()).toBe(true)
  })
})
