import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/_authenticated/settings/notifications')({
  beforeLoad: () => {
    throw redirect({
      to: '/workspace/$view',
      params: { view: 'notification-preferences' },
    })
  },
})
