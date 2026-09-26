export const MAX_PDF_UPLOAD_BYTES = 512 * 1024 * 1024

export type PdfImportStage = 'uploading' | 'saving' | 'parsing'

export interface PdfImportProgress {
  active: boolean
  fileName: string
  fileSize: number
  stage: PdfImportStage
  uploadPercent: number
  parsePercent: number
}

export function createPdfImportProgress(file: File): PdfImportProgress {
  return {
    active: true,
    fileName: file.name,
    fileSize: file.size,
    stage: 'uploading',
    uploadPercent: 0,
    parsePercent: 0,
  }
}

export function formatByteSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function pdfUploadTimeoutMs(fileSize: number): number {
  const megabytes = Math.max(1, Math.ceil(fileSize / (1024 * 1024)))
  return Math.min(15 * 60_000, Math.max(180_000, megabytes * 8_000 + 60_000))
}

export function uploadPercentFromEvent(loaded: number, total: number, fileSize: number): number {
  const size = total > 0 ? total : fileSize
  if (size <= 0) return 0
  return Math.min(100, Math.round((loaded / size) * 100))
}
