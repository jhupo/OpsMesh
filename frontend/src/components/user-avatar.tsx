import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { avatarQueryOptions, type CurrentUser } from '@/api/auth'
import { getDisplayNameInitials } from '@/lib/utils'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'

export function UserAvatar({
  user,
  preview,
  className,
}: {
  user: CurrentUser
  preview?: string | null
  className?: string
}) {
  const { data } = useQuery(avatarQueryOptions(user))
  const [source, setSource] = useState<{ blob: Blob; url: string }>()
  useEffect(() => {
    if (!data) return
    const reader = new FileReader()
    reader.onload = () => setSource({ blob: data, url: String(reader.result) })
    reader.readAsDataURL(data)
    return () => {
      reader.onload = null
      reader.abort()
    }
  }, [data])
  const stored = source?.blob === data ? source?.url : undefined
  return (
    <Avatar className={className}>
      <AvatarImage
        src={preview === undefined ? stored : (preview ?? undefined)}
        alt=''
      />
      <AvatarFallback>
        {getDisplayNameInitials(user.display_name)}
      </AvatarFallback>
    </Avatar>
  )
}
