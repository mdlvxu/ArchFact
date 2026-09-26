import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import LanguageToggle from '@/components/business/LanguageToggle.vue'
import {
  localizeProcessingLog,
  localizeKnownText,
  setLocale,
  translate,
} from '@/i18n'

afterEach(async () => {
  setLocale('en-US')
  await nextTick()
})

describe('界面语言切换', () => {
  it('切换语言并记住用户选择', async () => {
    setLocale('en-US')
    const wrapper = mount(LanguageToggle)

    expect(wrapper.find('.language-toggle__active').text()).toBe('EN')
    await wrapper.trigger('click')
    await nextTick()

    expect(wrapper.find('.language-toggle__active').text()).toBe('中')
    expect(wrapper.attributes('title')).toBe('当前语言：中文')
    expect(globalThis.localStorage.getItem('archfact-locale')).toBe('zh-CN')
    expect(globalThis.document.documentElement.lang).toBe('zh-CN')
  })

  it('只翻译界面显示，不改写 PDF 与抽取业务数据', () => {
    const businessData = {
      fileName: '考古报告.pdf',
      template: { name: 'Basic Research Template', labels: ['Artifact ID'] },
      annotation: { quote: '原始标注文字', imageUrl: '/storage/pages/001.jpg' },
    }
    const snapshot = structuredClone(businessData)

    setLocale('zh-CN')

    expect(translate('nav.inputPdf')).toBe('导入 PDF')
    expect(localizeKnownText(businessData.template.name)).toBe('基础研究模板')
    expect(businessData).toEqual(snapshot)
  })

  it('在英文界面本地化已保存的后端处理日志，不改写原始日志', () => {
    const rawLog = '第 12 页预处理完成，等待语义抽取'
    setLocale('en-US')

    expect(localizeProcessingLog(rawLog)).toBe(
      'Page 12 preprocessing completed; awaiting semantic extraction',
    )
    expect(rawLog).toBe('第 12 页预处理完成，等待语义抽取')

    setLocale('zh-CN')
    expect(localizeProcessingLog(rawLog)).toBe(rawLog)
  })
})
