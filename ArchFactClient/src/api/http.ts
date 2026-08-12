import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'
import { translate } from '@/i18n'

export interface RequestOptions extends AxiosRequestConfig {
  /** 由调用方自行处理的预期错误（例如恢复已失效的本地任务）不显示全局提示。 */
  suppressErrorMessage?: boolean
}

/** 后端统一响应结构 */
export interface ApiResponse<T = unknown> {
  code: number
  message: string
  data: T
}

/** 业务请求错误，携带后端返回的错误信息 */
export class ApiError extends Error {
  constructor(
    message: string,
    public code: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

// 创建 Axios 实例，所有 API 请求必须通过此实例
const http: AxiosInstance = axios.create({
  // 未配置本地环境变量时默认使用 Vite 代理地址，保证初始化项目可直接运行
  baseURL: import.meta.env.VITE_API_BASE_URL ?? '/api',
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 请求拦截器：自动附加 Token（从 localStorage 读取）
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：统一处理 HTTP 状态码和业务错误码
http.interceptors.response.use(
  (response: AxiosResponse<ApiResponse>) => {
    const { code, message, data } = response.data

    // 业务成功：直接返回 data 部分
    if (code === 0 || code === 200) {
      return data as unknown as AxiosResponse
    }

    // 业务失败：抛出带错误码的异常
    return Promise.reject(new ApiError(message || '请求失败', code))
  },
  (error: unknown) => {
    if (axios.isAxiosError(error)) {
      const status = error.response?.status
      const requestOptions = error.config as RequestOptions | undefined

      if (!requestOptions?.suppressErrorMessage) {
        // 公共 HTTP 错误统一提示
        if (status === 401) {
          ElMessage.error(translate('api.unauthorized'))
        } else if (status === 403) {
          ElMessage.error(translate('api.forbidden'))
        } else if (status === 500) {
          ElMessage.error(translate('api.serverError'))
        } else {
          ElMessage.error(error.message || translate('api.networkError'))
        }
      }
    }

    return Promise.reject(error)
  },
)

/** 封装 GET 请求，返回解析后的 data */
export function get<T>(url: string, config?: RequestOptions): Promise<T> {
  return http.get<ApiResponse<T>, T>(url, config)
}

/** 封装 POST 请求，返回解析后的 data */
export function post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return http.post<ApiResponse<T>, T>(url, data, config)
}

/** 封装 PUT 请求，返回解析后的 data */
export function put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return http.put<ApiResponse<T>, T>(url, data, config)
}

/** 封装 PATCH 请求，用于部分更新记录状态等资源。 */
export function patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return http.patch<ApiResponse<T>, T>(url, data, config)
}

/** 封装 DELETE 请求，返回解析后的 data */
export function del<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
  return http.delete<ApiResponse<T>, T>(url, config)
}

export default http
