import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import VersionHistory from '@/components/business/VersionHistory.vue'
import type { VerificationVersion } from '@/types/verification'

function version(matchingVersionId: string): VerificationVersion {
  return {
    version: 1,
    createdAt: '2026-10-29 12:00',
    title: 'Version 1',
    summary: '3 effective assertions · 423 full records · 18 fixed human samples',
    matchingVersionId,
    staleCount: 0,
    relationChangedCount: 0,
    exportable: true,
    before: 'No previous version.',
    after: 'Alignment 90%, 42 records require review.',
    impact: {
      alignmentBefore: 0,
      alignmentAfter: 90,
      errorsBefore: 0,
      errorsAfter: 42,
      passedBefore: 0,
      passedAfter: 381,
    },
  }
}

describe('VersionHistory matching-version label', () => {
  it('hides M0 and displays a rematched version', () => {
    const initial = mount(VersionHistory, { props: { versions: [version('M0')] } })
    expect(initial.text()).not.toContain('M0')

    const rematched = mount(VersionHistory, { props: { versions: [version('rematch-2')] } })
    expect(rematched.text()).toContain('rematch-2')
  })
})
