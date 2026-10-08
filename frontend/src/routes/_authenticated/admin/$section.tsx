import { z } from 'zod'
import { Outlet, createFileRoute } from '@tanstack/react-router'

const adminSearchSchema = z
  .object({
    page: z.number().optional().catch(1),
    pageSize: z.number().optional().catch(10),
    status: z
      .array(z.enum(['active', 'inactive']))
      .optional()
      .catch([]),
    username: z.string().optional().catch(''),
  })
  .catchall(z.unknown())

export const Route = createFileRoute('/_authenticated/admin/$section')({
  validateSearch: adminSearchSchema,
  component: Outlet,
})
