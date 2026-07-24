import type {
  ExtractionConfigPayload,
  ExtractionFieldType,
  ExtractionTemplate,
  ExtractionTemplateField,
  PostProcessingRule,
} from '@/types/extraction'

interface BuildExtractionConfigInput {
  template: ExtractionTemplate
  constraints: ExtractionTemplateField[]
  rules: PostProcessingRule[]
  pages: number[]
}

export const fieldTypeMap = {
  Num: 'number',
  Text: 'string',
  Date: 'date',
  'Yes/No': 'boolean',
  Image: 'image',
  Obj: 'object',
  Arr: 'array',
} satisfies Record<ExtractionTemplateField['type'], ExtractionFieldType>

export const constraintTypeMap: Record<ExtractionFieldType, ExtractionTemplateField['type']> = {
  number: 'Num',
  string: 'Text',
  date: 'Date',
  boolean: 'Yes/No',
  image: 'Image',
  object: 'Obj',
  array: 'Arr',
}

/** 为新字段生成一次性稳定 key；中文标签使用随机后缀，避免依赖显示名称。 */
export function createFieldKey(label: string, existingKeys: Iterable<string> = []) {
  const usedKeys = new Set(existingKeys)
  const normalized = label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  const baseKey = normalized && /^[a-z]/.test(normalized)
    ? normalized
    : `field_${globalThis.crypto?.randomUUID?.().replace(/-/g, '').slice(0, 10) ?? Date.now()}`
  let key = baseKey
  let suffix = 2
  while (usedKeys.has(key)) {
    key = `${baseKey}_${suffix}`
    suffix += 1
  }
  return key
}

/** 把界面状态编译成稳定的任务契约，是 UI 与后端之间唯一的转换入口。 */
export function buildExtractionConfig(
  input: BuildExtractionConfigInput,
): ExtractionConfigPayload {
  return {
    schemaVersion: '1.0',
    pipelineId: 'default',
    templateId: input.template.id,
    templateName: input.template.name,
    fields: input.constraints.map((field) => ({
      key: field.key,
      label: field.label,
      type: fieldTypeMap[field.type],
      required: field.required,
      instruction: field.instruction?.trim() || undefined,
      evidence_kind: field.evidence_kind ?? undefined,
    })),
    postProcessingRules: input.rules
      .filter((rule) => rule.enabled)
      .map((rule) => ({
        key: rule.key,
        name: rule.name,
        description: rule.description,
        example: rule.example,
        handler: rule.handler,
      })),
    pages: [...input.pages],
  }
}
