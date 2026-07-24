import { describe, it, expect } from 'vitest'
import http from '@/api/http'

describe('项目基础配置', () => {
  it('未配置环境变量时应使用默认 API 地址', () => {
    expect(http.defaults.baseURL).toBe('/api')
  })
})
