import Icon from './Icon'

const POINTS = [
  ['leaf', '100% vegetarian', 'A pure-veg kitchen, always'],
  ['fire', 'Cooked fresh', 'Made to order, every day'],
  ['moon', 'Open late', '1 PM to 1 AM, all week'],
  ['bike', 'Delivered', 'On Swiggy & Zomato'],
]

export default function Promise() {
  return (
    <div className="promise">
      <div className="wrap promise-grid">
        {POINTS.map(([icon, title, sub]) => (
          <div key={title}>
            <Icon name={icon} size={28} />
            <span><b>{title}</b><small>{sub}</small></span>
          </div>
        ))}
      </div>
    </div>
  )
}
