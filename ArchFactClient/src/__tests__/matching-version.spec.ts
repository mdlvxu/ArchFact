import { describe, expect, it } from 'vitest'
import { displayMatchingVersion, joinVisibleMetadata } from '@/domain/matching-version'

describe('matching-version display', () => {
  it('hides the default matching version while retaining actual rematch versions', () => {
    expect(displayMatchingVersion('M0')).toBe('')
    expect(displayMatchingVersion(' m0 ')).toBe('')
    expect(displayMatchingVersion('rematch-2')).toBe('rematch-2')
  })

  it('joins visible metadata without leaving separators for hidden values', () => {
    expect(joinVisibleMetadata('Basic Research', displayMatchingVersion('M0'), '2026-10-29'))
      .toBe('Basic Research · 2026-10-29')
    expect(joinVisibleMetadata('Basic Research', displayMatchingVersion('rematch-2')))
      .toBe('Basic Research · rematch-2')
  })
})
