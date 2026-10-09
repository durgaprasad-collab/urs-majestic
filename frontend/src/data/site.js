import { CATEGORIES } from './menu'

export const PHONE = '+919150102001'
export const PHONE_DISPLAY = '+91 91501 02001'
export const WHATSAPP = 'https://wa.me/919150102001'
export const SWIGGY = 'https://www.swiggy.com/city/chennai/urs-majestic-pallavaram-rest1389673'
export const ZOMATO = 'http://zoma.to/r/22783970'
export const DIRECTIONS = 'https://www.google.com/maps/dir/?api=1&destination=12.951982,80.170999'
export const MAP_EMBED =
  'https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d3888.310354222551!2d80.17099897373143!3d12.951981815310795!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x3a525f2fc0404461%3A0x88b26c8f4fbd6c6!2sURS%20MAJESTIC!5e0!3m2!1sen!2sin!4v1782284251147!5m2!1sen!2sin'

// Opening hours, IST. Closes after midnight, so "open" spans two calendar days.
export const OPEN_HOUR = 13
export const CLOSE_HOUR = 1

const ITEMS = CATEGORIES.flatMap((c) => c.items)

// Price straight from the menu data, so featured cards can't drift from the menu.
export function priceOf(name) {
  const item = ITEMS.find((i) => i.name === name)
  if (!item) throw new Error(`priceOf: "${name}" is not on the menu`)
  return item.price
}
