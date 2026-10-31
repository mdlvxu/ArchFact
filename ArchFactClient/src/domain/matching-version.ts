export function displayMatchingVersion(value?: string | null): string {
  const version = value?.trim()
  return version && version.toUpperCase() !== 'M0' ? version : ''
}

export function joinVisibleMetadata(...values: Array<string | null | undefined>): string {
  return values.map((value) => value?.trim()).filter(Boolean).join(' · ')
}
