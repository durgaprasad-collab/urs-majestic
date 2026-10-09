import { CATEGORIES } from '../data/menu'

// Price comes from the menu data so this strip can't drift from the menu.
const combo = CATEGORIES.flatMap((c) => c.items).find((i) => i.name === 'Paneer Power Combo')

export default function Bestseller() {
  return (
    <div className="bestseller">
      <p className="bestseller-label">Most Ordered</p>
      <p className="bestseller-name">Paneer Power Combo</p>
      <p className="bestseller-sub">Butter Naan + Butter Masala + Paneer 65 &nbsp;·&nbsp; ₹{combo.price}</p>
    </div>
  )
}
