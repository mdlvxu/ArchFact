import { describe, expect, it } from 'vitest'
import router from '@/router'

describe('workflow workspace routes', () => {
  it.each([
    ['/', 'extraction', 'Data Extraction'],
    ['/preview', 'preview', 'Data Preview'],
    ['/machine-verification', 'machine-verification', 'Machine Verification'],
    ['/gt-evaluation', 'gt-evaluation', 'Cross-report Evaluation'],
    ['/insights', 'insights', 'Data Insights'],
  ])('resolves %s as the %s workspace route', (path, name, workspaceTab) => {
    const resolved = router.resolve(path)
    expect(resolved.name).toBe(name)
    expect(resolved.meta.workspaceTab).toBe(workspaceTab)
  })
})
