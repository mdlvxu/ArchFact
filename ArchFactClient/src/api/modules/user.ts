import { z } from 'zod'
import { get } from '@/api/http'

/** 用户信息的数据结构校验规则 */
const userSchema = z.object({
  id: z.number(),
  name: z.string(),
  email: z.string().email(),
})

/** 用户信息类型（由 Zod schema 推导） */
export type User = z.infer<typeof userSchema>

/** 获取当前登录用户信息 */
export async function fetchCurrentUser(): Promise<User> {
  const data = await get<unknown>('/user/current')
  // 使用 Zod 校验后端返回数据，避免直接断言类型
  return userSchema.parse(data)
}
