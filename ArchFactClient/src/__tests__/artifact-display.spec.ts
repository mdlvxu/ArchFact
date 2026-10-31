import { describe, expect, it } from 'vitest'
import { formatArtifactId } from '@/domain/artifact-display'

describe('artifact ID display format', () => {
  it('renders stored context and sequence IDs with a colon', () => {
    expect(formatArtifactId('M1,1')).toBe('M1:1')
    expect(formatArtifactId('AI(M17,9)')).toBe('AI(M17:9)')
  })

  it('leaves already formatted IDs and unrelated commas unchanged', () => {
    expect(formatArtifactId('M1:19')).toBe('M1:19')
    expect(formatArtifactId('Group A, north')).toBe('Group A, north')
  })
})
