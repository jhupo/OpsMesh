import { z } from 'zod'

const userStatusSchema = z.union([
  z.literal('active'),
  z.literal('inactive'),
  z.literal('invited'),
  z.literal('suspended'),
])
export type UserStatus = z.infer<typeof userStatusSchema>

const userRoleSchema = z.union([
  z.literal('user'),
  z.literal('superadmin'),
  z.literal('admin'),
  z.literal('cashier'),
  z.literal('manager'),
])

const _userSchema = z.object({
  id: z.string(),
  firstName: z.string(),
  lastName: z.string(),
  displayName: z.string().optional(),
  invitationDeliveryStatus: z.string().nullable().optional(),
  username: z.string(),
  loginName: z.string().nullable().optional(),
  email: z.string(),
  phoneNumber: z.string(),
  status: userStatusSchema,
  role: userRoleSchema,
  platformAdmin: z.boolean().optional(),
  workspaceCount: z.number().optional(),
  activeWorkspaceCount: z.number().optional(),
  resourceUsageRate: z.number().optional(),
  createdAt: z.coerce.date(),
  updatedAt: z.coerce.date(),
})
export type User = z.infer<typeof _userSchema>
