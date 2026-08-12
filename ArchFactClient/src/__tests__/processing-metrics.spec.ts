import { describe, expect, it } from 'vitest'
import { buildProcessingMetrics } from '@/domain/processing-metrics'

describe('Processing Progress metrics', () => {
  it('calculates live metrics from backend timestamps and completed pages', () => {
    const metrics = buildProcessingMetrics({
      startedAt: '2026-07-16T10:20:00',
      endedAt: null,
      processedPages: 2,
      totalPages: 5,
      running: true,
      nowMs: Date.parse('2026-07-16T10:22:00Z'),
    })

    expect(metrics).toMatchObject({
      elapsedTime: '2m 0s',
      timeLeft: '~ 3m 0s',
      processingRate: '1 page/min',
    })
  })

  it('updates elapsed time, rate, and remaining time as the clock advances', () => {
    const metrics = buildProcessingMetrics({
      startedAt: '2026-07-16T10:20:00',
      endedAt: null,
      processedPages: 2,
      totalPages: 5,
      running: true,
      nowMs: Date.parse('2026-07-16T10:23:00Z'),
    })

    expect(metrics.elapsedTime).toBe('3m 0s')
    expect(metrics.processingRate).toBe('0.67 pages/min')
    expect(metrics.timeLeft).toBe('~ 4m 30s')
  })

  it('freezes metrics using the backend end time after completion', () => {
    const metrics = buildProcessingMetrics({
      startedAt: '2026-07-16T10:20:00',
      endedAt: '2026-07-16T10:22:30',
      processedPages: 5,
      totalPages: 5,
      running: false,
      nowMs: Date.parse('2026-07-16T12:00:00Z'),
    })

    expect(metrics.elapsedTime).toBe('2m 30s')
    expect(metrics.timeLeft).toBe('0s')
    expect(metrics.processingRate).toBe('2 pages/min')
  })

  it('does not inflate elapsed when wall clock is far past a frozen end time', () => {
    // Simulates rematch bumping updated_at days later while completed_at stays fixed.
    const metrics = buildProcessingMetrics({
      startedAt: '2026-07-23T13:45:57',
      endedAt: '2026-07-23T15:10:00',
      processedPages: 275,
      totalPages: 275,
      running: false,
      nowMs: Date.parse('2026-07-30T06:00:00Z'),
      locale: 'zh-CN',
    })

    expect(metrics.elapsedTime).toBe('1小时 24分 3秒')
    expect(metrics.timeLeft).toBe('0秒')
    expect(metrics.processingRate).not.toBe('0.03 页/分钟')
  })

  it('shows a calculating state before enough progress is available', () => {
    const metrics = buildProcessingMetrics({
      startedAt: '2026-07-16T10:20:00',
      endedAt: null,
      processedPages: 0,
      totalPages: 5,
      progress: 5,
      running: true,
      nowMs: Date.parse('2026-07-16T10:20:10Z'),
    })

    expect(metrics.processingRate).toBe('—')
    expect(metrics.timeLeft).toBe('Calculating…')
  })

  it('treats timezone-less backend timestamps as UTC and estimates short tasks by progress', () => {
    const metrics = buildProcessingMetrics({
      startedAt: '2026-07-17T05:21:00',
      endedAt: null,
      processedPages: 1,
      totalPages: 4,
      progress: 35,
      running: true,
      nowMs: Date.parse('2026-07-17T05:21:15Z'),
    })

    expect(metrics.elapsedTime).toBe('15s')
    expect(metrics.timeLeft).toBe('~ 30s')
    expect(metrics.processingRate).toBe('5.6 pages/min')
  })
})
