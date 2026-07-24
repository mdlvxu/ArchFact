import { describe, expect, it } from 'vitest'
import { buildExtractionConfig } from '@/domain/extraction-config'

describe('ExtractionSettings 请求契约', () => {
  it('开始抽取时提交模板、字段、规则和选择页的完整快照', async () => {
    const config = buildExtractionConfig({
      template: {
        id: 'basic-research',
        name: 'Basic Research Template',
        fields: [
          {
            key: 'artifact_id',
            label: 'Artifact ID',
            type: 'Text',
            required: true,
            instruction: 'Extract the catalog identifier.',
          },
        ],
      },
      constraints: [
        {
          key: 'artifact_id',
          label: 'Artifact ID',
          type: 'Text',
          required: true,
          instruction: 'Extract the catalog identifier.',
        },
      ],
      rules: [
        {
          id: 'chinese-number-to-arabic',
          key: 'chinese_number_to_arabic',
          name: 'Chinese Number',
          description: 'Convert numbers',
          example: '一 to 1',
          handler: 'builtin',
          enabled: true,
        },
        {
          id: 'unit-standardization',
          key: 'unit_standardization',
          name: 'Unit',
          description: 'Normalize units',
          example: '厘米 to cm',
          handler: 'builtin',
          enabled: true,
        },
        {
          id: 'punctuation-normalization',
          key: 'punctuation_normalization',
          name: 'Punctuation',
          description: 'Normalize punctuation',
          example: '， to ,',
          handler: 'builtin',
          enabled: true,
        },
      ],
      pages: [2],
    })

    expect(config.schemaVersion).toBe('1.0')
    expect(config.templateId).toBe('basic-research')
    expect(config.pages).toEqual([2])
    expect(config.fields[0]).toMatchObject({
      key: 'artifact_id',
      label: 'Artifact ID',
      type: 'string',
      required: true,
      instruction: 'Extract the catalog identifier.',
    })
    expect(config.postProcessingRules.map((rule) => rule.key)).toEqual([
      'chinese_number_to_arabic',
      'unit_standardization',
      'punctuation_normalization',
    ])
  })
})
