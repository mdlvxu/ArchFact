/** Render the stored context/sequence separator using archaeological notation. */
export function formatArtifactId(value: string) {
  return value.replace(/[,，]\s*(\d+)(?=\s*\)*\s*$)/, ':$1')
}
