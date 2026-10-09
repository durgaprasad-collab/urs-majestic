import { useEffect, useState } from 'react'
import { OPEN_HOUR, CLOSE_HOUR } from './data/site'

function isOpenNow() {
  // Hour in India regardless of the visitor's own timezone.
  const hour = Number(
    new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hourCycle: 'h23', timeZone: 'Asia/Kolkata' }).format(new Date()),
  )
  return hour >= OPEN_HOUR || hour < CLOSE_HOUR
}

export default function useOpenStatus() {
  const [open, setOpen] = useState(isOpenNow)
  useEffect(() => {
    const id = setInterval(() => setOpen(isOpenNow()), 60_000)
    return () => clearInterval(id)
  }, [])
  return open
}
