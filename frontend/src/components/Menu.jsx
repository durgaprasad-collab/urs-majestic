import { useState } from 'react'
import Icon from './Icon'
import { CATEGORIES } from '../data/menu'

function Item({ item }) {
  return (
    <li className="item">
      <span className="item-name">
        <span className="veg" aria-hidden="true" />
        {item.name}
        {item.bestseller && <span className="tag tag-best">Best seller</span>}
        {(item.tags || []).map((t) => <span key={t} className="tag">{t}</span>)}
      </span>
      <span className="item-lead" aria-hidden="true" />
      <span className="item-price">₹{item.price}</span>
    </li>
  )
}

export default function Menu() {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(CATEGORIES[0].id)
  const q = query.trim().toLowerCase()
  const visible = CATEGORIES
    .map((c) => ({ ...c, items: q ? c.items.filter((i) => i.name.toLowerCase().includes(q)) : c.items }))
    .filter((c) => c.items.length)

  return (
    <section className="section menu-section" id="menu">
      <div className="wrap">
        <div className="section-head">
          <div><span className="orn eyebrow">Full menu</span><h2>Our menu</h2></div>
          <p>Everything is vegetarian. Prices exclude taxes.</p>
        </div>

        <div className="menu-bar">
          <label className="menu-search">
            <Icon name="search" size={17} />
            <input type="search" placeholder="Search dishes…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search dishes" />
          </label>
          <nav className="menu-pills" aria-label="Menu categories">
            {CATEGORIES.map((c) => (
              <a key={c.id} href={`#cat-${c.id}`} className={active === c.id ? 'on' : ''} onClick={() => { setQuery(''); setActive(c.id) }}>
                {c.name}
              </a>
            ))}
          </nav>
        </div>

        <div className="menu-grid">
          {visible.map((c) => (
            <div key={c.id} id={`cat-${c.id}`} className="menu-cat">
              <h3>{c.name}{c.note && <small>{c.note}</small>}</h3>
              <ul role="list">{c.items.map((i) => <Item key={i.name} item={i} />)}</ul>
            </div>
          ))}
        </div>
        {!visible.length && <p className="menu-empty">No dish matches that. Try “paneer” or “biryani”.</p>}
        <p className="menu-note">All prices are exclusive of taxes.</p>
      </div>
    </section>
  )
}
