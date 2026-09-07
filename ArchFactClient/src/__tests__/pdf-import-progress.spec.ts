import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import PageNavigator from '@/components/business/PageNavigator.vue'
import {
  createPdfImportProgress,
  formatByteSize,
  pdfUploadTimeoutMs,
  uploadPercentFromEvent,
} from '@/domain/pdf-import'
import { setLocale } from '@/i18n'

afterEach(() => {
  setLocale('en-US')
})

describe('PDF 导入进度', () => {
  it('按文件大小拉长上传超时，避免大报告 120 秒被掐断', () => {
    expect(pdfUploadTimeoutMs(10 * 1024 * 1024)).toBe(180_000)
    expect(pdfUploadTimeoutMs(119 * 1024 * 1024)).toBe(15 * 60_000)
  })

  it('把字节数显示成可读大小', () => {
    expect(formatByteSize(119 * 1024 * 1024)).toBe('119.0 MB')
  })

  it('根据 XMLHttpRequest 进度计算百分比', () => {
    expect(uploadPercentFromEvent(59.5 * 1024 * 1024, 119 * 1024 * 1024, 119 * 1024 * 1024)).toBe(50)
  })

  it('页面导航在导入中显示上传和解析进度', () => {
    setLocale('zh-CN')
    const file = new File([new Uint8Array(8)], 'wenjiashan.pdf', { type: 'application/pdf' })
    const progress = {
      ...createPdfImportProgress(file),
      fileSize: 119 * 1024 * 1024,
      uploadPercent: 42,
      parsePercent: 18,
      stage: 'uploading' as const,
    }
    const wrapper = mount(PageNavigator, {
      props: {
        pages: [],
        activePage: 1,
        total: 0,
        fileName: 'wenjiashan.pdf',
        importProgress: progress,
      },
    })

    expect(wrapper.text()).toContain('正在上传到服务器')
    expect(wrapper.text()).toContain('已上传 42%')
    expect(wrapper.text()).toContain('已解析 18%')
    expect(wrapper.text()).toContain('119.0 MB')
  })

  it('导入结束后重新观察缩略图，避免进度条消失后左侧空白', async () => {
    setLocale('zh-CN')
    const observed: number[] = []
    class FakeObserver {
      observe(element: Element) {
        const page = Number((element as HTMLElement).dataset.page)
        if (page) observed.push(page)
      }
      disconnect() {}
      unobserve() {}
    }
    const previousObserver = globalThis.IntersectionObserver
    globalThis.IntersectionObserver = FakeObserver as unknown as typeof IntersectionObserver

    try {
      const file = new File([new Uint8Array(8)], 'report.pdf', { type: 'application/pdf' })
      const wrapper = mount(PageNavigator, {
        props: {
          pages: [],
          activePage: 1,
          total: 0,
          fileName: 'report.pdf',
          importProgress: createPdfImportProgress(file),
        },
      })

      await wrapper.setProps({
        importProgress: null,
        pages: [
          { page: 1, thumbnailUrl: '', loading: false },
          { page: 2, thumbnailUrl: '', loading: false },
        ],
        total: 2,
      })
      await wrapper.vm.$nextTick()
      await Promise.resolve()
      await wrapper.vm.$nextTick()

      expect(observed).toEqual([1, 2])
    } finally {
      globalThis.IntersectionObserver = previousObserver
    }
  })
})
