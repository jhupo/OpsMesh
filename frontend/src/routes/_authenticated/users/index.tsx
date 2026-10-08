import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/users/')({
  beforeLoad: () => {
    throw redirect({
      to: '/admin/$section',
      params: { section: 'users' },
      replace: true,
    })
  },
})
